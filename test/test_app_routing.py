import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import assistant
except ModuleNotFoundError as error:
    if error.name != "ollama":
        raise
    ollama_stub = types.ModuleType("ollama")
    ollama_stub.chat = lambda **kwargs: None
    sys.modules["ollama"] = ollama_stub
    import assistant

import app_tools
import audit_log
import memory_tools


def _memory_tool_names() -> set[str]:
    return {tool["function"]["name"] for tool in memory_tools.TOOLS}

def _tool_call(name: str, arguments: dict[str, str]) -> dict[str, object]:
    return {
        "message": {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": name, "arguments": arguments}}
            ],
        }
    }


class AppRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit_directory = tempfile.TemporaryDirectory()
        self.audit_patch = patch.object(
            audit_log,
            "LOG_ROOT",
            Path(self.audit_directory.name),
        )
        self.audit_patch.start()
        self.addCleanup(self.audit_patch.stop)
        self.addCleanup(self.audit_directory.cleanup)

    def test_system_prompt_includes_person_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            memory_root = Path(temporary_directory)
            (memory_root / "personSummary.txt").write_text(
                "Prefers concise explanations.", encoding="utf-8"
            )
            with patch.object(memory_tools, "MEMORY_ROOT", memory_root):
                prompt = assistant.build_system_prompt()

        self.assertIn("## Personal summary", prompt)
        self.assertIn("Prefers concise explanations.", prompt)

    def test_missing_and_empty_summary_have_explicit_prompt_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                memory_tools, "MEMORY_ROOT", Path(temporary_directory)
            ):
                missing_prompt = assistant.build_system_prompt()
                (Path(temporary_directory) / "personSummary.txt").write_text(
                    "", encoding="utf-8"
                )
                empty_prompt = assistant.build_system_prompt()

        fallback = "No personal summary has been recorded yet."
        self.assertIn(fallback, missing_prompt)
        self.assertIn(fallback, empty_prompt)

    def test_typed_session_uses_one_summary_snapshot(self) -> None:
        prompt_seen_by_turns: list[str] = []

        def fake_run_assistant(history: list[dict[str, str]]) -> str:
            prompt_seen_by_turns.append(history[0]["content"])
            return "Done."

        with patch.object(
            assistant, "build_system_prompt", return_value="session snapshot"
        ) as build_prompt:
            with patch("builtins.input", side_effect=["first", "second", "exit"]):
                with patch.object(
                    assistant, "run_assistant", side_effect=fake_run_assistant
                ):
                    assistant.notes()

        build_prompt.assert_called_once_with()
        self.assertEqual(prompt_seen_by_turns, ["session snapshot", "session snapshot"])

    def test_obsidian_turn_only_receives_selection_then_obsidian_tools(self) -> None:
        responses = [
            _tool_call("select_app", {"app_id": "obsidian"}),
            _tool_call(
                "create_obsidian_document",
                {"path": "Stories/scene.md", "content": "The rain stopped."},
            ),
            {"message": {"role": "assistant", "content": "Created the scene."}},
        ]
        sent_tool_names: list[list[str]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_tool_names.append(
                [tool["function"]["name"] for tool in kwargs["tools"]]
            )
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temporary_directory:
            vault_root = Path(temporary_directory)
            with patch.object(
                app_tools.APP_TOOLSETS["obsidian"], "VAULT_ROOT", vault_root
            ):
                with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
                    history = [
                        {"role": "user", "content": "Write a scene in Obsidian."}
                    ]
                    answer = assistant.run_assistant(history)
                    self.assertEqual(
                        (vault_root / "Stories/scene.md").read_text(),
                        "The rain stopped.",
                    )

        self.assertEqual(answer, "Created the scene.")
        self.assertEqual(set(sent_tool_names[0]), _memory_tool_names() | {"select_app"})
        self.assertTrue(sent_tool_names[1])
        self.assertTrue(
            all(
                name.startswith(
                    (
                        "create_obsidian_",
                        "read_obsidian_",
                        "edit_obsidian_",
                        "delete_obsidian_",
                        "show_obsidian_",
                        "list_obsidian_",
                        "search_obsidian_",
                    )
                )
                for name in set(sent_tool_names[1]) - _memory_tool_names()
            )
        )
        self.assertTrue(_memory_tool_names().issubset(set(sent_tool_names[1])))
        self.assertEqual(sent_tool_names[1], sent_tool_names[2])

    def test_notes_turn_only_receives_selection_then_notes_tools(self) -> None:
        responses = [
            _tool_call("select_app", {"app_id": "apple_notes"}),
            _tool_call("create_note", {"title": "Idea", "body": "A story idea."}),
            {"message": {"role": "assistant", "content": "Created the note."}},
        ]
        sent_tool_names: list[list[str]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_tool_names.append(
                [tool["function"]["name"] for tool in kwargs["tools"]]
            )
            return responses.pop(0)

        with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
            with patch.object(
                app_tools.APP_TOOLSETS["apple_notes"],
                "dispatch",
                return_value="created",
            ) as dispatch:
                history = [
                    {"role": "user", "content": "Create a note in Apple Notes."}
                ]
                answer = assistant.run_assistant(history)

        self.assertEqual(answer, "Created the note.")
        self.assertEqual(set(sent_tool_names[0]), _memory_tool_names() | {"select_app"})
        self.assertEqual(
            set(sent_tool_names[1]) - _memory_tool_names(),
            {
                tool["function"]["name"]
                for tool in app_tools.APP_TOOLSETS["apple_notes"].TOOLS
            },
        )
        self.assertTrue(_memory_tool_names().issubset(set(sent_tool_names[1])))
        self.assertEqual(sent_tool_names[1], sent_tool_names[2])
        self.assertFalse(
            any(name.startswith("create_obsidian_") for name in sent_tool_names[1])
        )
        dispatch.assert_called_once()

    def test_app_selection_tool_is_optional(self) -> None:
        with patch.object(
            assistant.ollama,
            "chat",
            return_value={"message": {"content": "Which app would you like to use?"}},
        ) as chat:
            answer = assistant.run_assistant(
                [{"role": "user", "content": "I need help with something."}]
            )

        self.assertEqual(answer, "Which app would you like to use?")
        self.assertEqual(
            {tool["function"]["name"] for tool in chat.call_args.kwargs["tools"]},
            _memory_tool_names() | {"select_app"},
        )

    def test_memory_tool_dispatches_before_and_after_app_selection(self) -> None:
        responses = [
            _tool_call("read_memory", {"path": "personSummary.txt"}),
            _tool_call("select_app", {"app_id": "calendar"}),
            _tool_call(
                "list_calendar_events",
                {"start_date": "2026-11-08", "end_date": "2026-11-08"},
            ),
            {"message": {"role": "assistant", "content": "Done."}},
        ]
        sent_tool_names: list[set[str]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_tool_names.append(
                {tool["function"]["name"] for tool in kwargs["tools"]}
            )
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(memory_tools, "MEMORY_ROOT", Path(temporary_directory)):
                with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
                    with patch.object(
                        app_tools.APP_TOOLSETS["calendar"],
                        "dispatch",
                        return_value="[]",
                    ) as dispatch:
                        answer = assistant.run_assistant(
                            [{"role": "user", "content": "Check my memory and calendar."}]
                        )

        self.assertEqual(answer, "Done.")
        self.assertTrue(all(_memory_tool_names().issubset(names) for names in sent_tool_names))
        self.assertIn("select_app", sent_tool_names[0])
        self.assertIn("list_calendar_events", sent_tool_names[2])
        self.assertNotIn("list_calendar_events", sent_tool_names[0])
        dispatch.assert_called_once()

    def test_available_tools_follow_the_registered_context(self) -> None:
        context = app_tools.ToolContext()
        self.assertEqual(
            [
                tool["function"]["name"]
                for tool in app_tools.get_available_tools(context)
            ],
            ["select_app"],
        )

        result = app_tools.dispatch_tool(
            "select_app",
            {"app_id": "calendar"},
            context,
            available_tool_names={"select_app"},
        )
        self.assertIn("Selected Calendar", result)

        available = app_tools.get_available_tools(context)
        expected = {
            definition["function"]["name"]
            for definition in app_tools.APP_TOOLSETS["calendar"].TOOLS
        }
        available_names = {tool["function"]["name"] for tool in available}
        self.assertEqual(available_names, expected)
        self.assertNotIn("select_app", available_names)


if __name__ == "__main__":
    unittest.main()
