import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
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
import server_time


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

    def test_each_turn_receives_fresh_server_local_datetime(self) -> None:
        local_times = [
            datetime(2026, 11, 9, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")),
            datetime(2026, 11, 9, 9, 16, tzinfo=ZoneInfo("Asia/Kolkata")),
        ]
        sent_messages: list[list[dict[str, str]]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_messages.append(
                [dict(message) for message in kwargs["messages"]]
            )
            return {"message": {"content": "Done."}}

        history = [{"role": "system", "content": "Session snapshot"}]
        with patch.object(server_time, "local_now", side_effect=local_times):
            with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
                assistant.run_assistant(history)
                history.append({"role": "user", "content": "What time is it?"})
                assistant.run_assistant(history)

        time_contexts = [
            [
                message["content"]
                for message in messages
                if message.get("role") == "system"
                and message.get("content", "").startswith(
                    "Current server-local date and time:"
                )
            ]
            for messages in sent_messages
        ]
        self.assertEqual(len(time_contexts[0]), 1)
        self.assertEqual(len(time_contexts[1]), 1)
        self.assertIn(local_times[0].isoformat(), time_contexts[0][0])
        self.assertIn(local_times[1].isoformat(), time_contexts[1][0])
        self.assertIn("do not assume UTC", time_contexts[1][0])

    def test_history_keeps_system_messages_and_recent_turns(self) -> None:
        sent_messages: list[list[dict[str, object]]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_messages.append(
                [dict(message) for message in kwargs["messages"]]
            )
            return {
                "message": {
                    "role": "assistant",
                    "content": "Detailed answer with all details intact.",
                }
            }

        history = [{"role": "system", "content": "Session instructions"}]
        for index in range(10):
            history.extend(
                [
                    {"role": "user", "content": f"request {index}"},
                    {"role": "assistant", "content": f"answer {index}"},
                ]
            )
        history.append({"role": "user", "content": "latest request"})

        with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
            answer = assistant.run_assistant(history)

        sent = sent_messages[0]
        sent_conversation = [message for message in sent if message["role"] != "system"]
        self.assertLessEqual(len(sent_conversation), assistant.MAX_HISTORY_MESSAGES)
        self.assertTrue(
            any(message["content"] == "Session instructions" for message in sent)
        )
        self.assertTrue(
            any(
                message["role"] == "system"
                and str(message["content"]).startswith(
                    "Current server-local date and time:"
                )
                for message in sent
            )
        )
        self.assertNotIn(
            "request 0",
            [message["content"] for message in sent_conversation],
        )
        self.assertEqual(sent_conversation[-1]["content"], "latest request")
        self.assertEqual(answer, "Detailed answer with all details intact.")
        self.assertEqual(
            history[-1]["content"], "Detailed answer with all details intact."
        )
        self.assertEqual(len(history), 23)

    def test_history_compaction_keeps_last_twenty_non_system_messages(self) -> None:
        history = [{"role": "system", "content": "Session instructions"}]
        for index in range(9):
            history.extend(
                [
                    {"role": "user", "content": f"request {index}"},
                    {"role": "assistant", "content": f"answer {index}"},
                ]
            )
        history.extend(
            [
                {"role": "user", "content": "create a note"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "create_note", "arguments": {}}}
                    ],
                },
                {"role": "tool", "name": "create_note", "content": "created"},
                {"role": "assistant", "content": "The note was created."},
            ]
        )
        conversation = [message for message in history if message["role"] != "system"]
        expected_tail = conversation[-assistant.MAX_HISTORY_MESSAGES :]

        assistant._compact_history(history)

        conversation = [message for message in history if message["role"] != "system"]
        self.assertEqual(conversation, expected_tail)
        self.assertEqual(len(conversation), assistant.MAX_HISTORY_MESSAGES)
        self.assertEqual(conversation[0]["content"], "request 1")

    def test_assistant_answer_is_replaced_by_last_tldr_in_history(self) -> None:
        answer = (
            "This is the complete answer.\n"
            "TLDR: an earlier summary\n"
            "TLDR: the final concise summary"
        )
        history = [{"role": "user", "content": "Help."}]
        with patch.object(
            assistant.ollama,
            "chat",
            return_value={"message": {"role": "assistant", "content": answer}},
        ):
            result = assistant.run_assistant(history)

        self.assertEqual(result, answer)
        self.assertEqual(history[-1]["content"], "the final concise summary")

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
        tool_contexts: list[object] = []

        def fake_run_assistant(
            history: list[dict[str, str]], **kwargs: object
        ) -> str:
            prompt_seen_by_turns.append(history[0]["content"])
            tool_contexts.append(kwargs["tool_context"])
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
        self.assertEqual(len(tool_contexts), 2)
        self.assertIs(tool_contexts[0], tool_contexts[1])

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
                for name in (
                    set(sent_tool_names[1]) - _memory_tool_names() - {"select_app"}
                )
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
            } | {"select_app"},
        )
        self.assertTrue(_memory_tool_names().issubset(set(sent_tool_names[1])))
        self.assertEqual(sent_tool_names[1], sent_tool_names[2])
        self.assertFalse(
            any(name.startswith("create_obsidian_") for name in sent_tool_names[1])
        )
        dispatch.assert_called_once()

    def test_selected_app_tools_persist_across_turns_and_can_switch(self) -> None:
        responses = [
            _tool_call("select_app", {"app_id": "apple_notes"}),
            {"message": {"role": "assistant", "content": "Notes selected."}},
            _tool_call("list_notes", {}),
            {"message": {"role": "assistant", "content": "Hobbies"}},
            _tool_call("select_app", {"app_id": "obsidian"}),
            {"message": {"role": "assistant", "content": "Obsidian selected."}},
            _tool_call(
                "create_obsidian_document",
                {"path": "Stories/scene.md", "content": "The rain stopped."},
            ),
            {"message": {"role": "assistant", "content": "Created the scene."}},
        ]
        sent_tool_names: list[set[str]] = []

        def fake_chat(**kwargs: object) -> dict[str, object]:
            sent_tool_names.append(
                {tool["function"]["name"] for tool in kwargs["tools"]}
            )
            return responses.pop(0)

        context = app_tools.ToolContext()
        with patch.object(assistant.ollama, "chat", side_effect=fake_chat):
            with patch.object(
                app_tools.APP_TOOLSETS["apple_notes"],
                "dispatch",
                return_value="Hobbies",
            ) as notes_dispatch:
                with patch.object(
                    app_tools.APP_TOOLSETS["obsidian"],
                    "dispatch",
                    return_value="created",
                ) as obsidian_dispatch:
                    self.assertEqual(
                        assistant.run_assistant(
                            [{"role": "user", "content": "Use Apple Notes."}],
                            tool_context=context,
                        ),
                        "Notes selected.",
                    )
                    self.assertEqual(
                        assistant.run_assistant(
                            [{"role": "user", "content": "List my notes."}],
                            tool_context=context,
                        ),
                        "Hobbies",
                    )
                    self.assertEqual(
                        assistant.run_assistant(
                            [{"role": "user", "content": "Switch to Obsidian."}],
                            tool_context=context,
                        ),
                        "Obsidian selected.",
                    )
                    self.assertEqual(
                        assistant.run_assistant(
                            [{"role": "user", "content": "Create a scene."}],
                            tool_context=context,
                        ),
                        "Created the scene.",
                    )

        self.assertIn("list_notes", sent_tool_names[2])
        self.assertIn("select_app", sent_tool_names[2])
        self.assertIn("list_notes", sent_tool_names[3])
        self.assertIn("select_app", sent_tool_names[4])
        self.assertIn("create_obsidian_document", sent_tool_names[5])
        self.assertIn("create_obsidian_document", sent_tool_names[6])
        self.assertIn("select_app", sent_tool_names[6])
        self.assertNotIn("list_notes", sent_tool_names[6])
        notes_dispatch.assert_called_once_with("list_notes", {})
        obsidian_dispatch.assert_called_once()

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
        self.assertEqual(available_names, expected | {"select_app"})


if __name__ == "__main__":
    unittest.main()
