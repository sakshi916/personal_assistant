import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    import assistant
except ModuleNotFoundError as error:
    if error.name != "ollama":
        raise
    import sys
    import types

    ollama_stub = types.ModuleType("ollama")
    ollama_stub.chat = lambda **kwargs: None
    sys.modules["ollama"] = ollama_stub
    import assistant

import app_tools
import audit_log
import memory_tools


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class AuditLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.log_patch = patch.object(audit_log, "LOG_ROOT", self.root / "logs")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_tool_arguments_are_limited_to_100_characters_and_results_logged(self) -> None:
        supplied_text = "payload:" + ("x" * 250)
        result = "tool result " * 40

        with audit_log.assistant_turn() as turn_id:
            with audit_log.tool_call("call-123"):
                audit_log.log_tool_call(
                    app_id="memory",
                    tool_name="custom_tool",
                    arguments={"unusual_payload_name": supplied_text},
                    result=result,
                    status="success",
                    duration_ms=1.25,
                )

        event = _read_events(self.root / "logs/memory.jsonl")[0]
        logged_input = event["arguments"]["unusual_payload_name"]
        self.assertEqual(len(logged_input), 100)
        self.assertTrue(logged_input.endswith("[truncated]"))
        self.assertEqual(event["result"], result)
        self.assertEqual(event["call_id"], "call-123")
        self.assertEqual(event["turn_id"], turn_id)
        self.assertEqual(event["status"], "success")

    def test_log_timestamp_uses_machine_local_timezone(self) -> None:
        audit_log.log_tool_call(
            app_id="memory",
            tool_name="local_time_test",
            arguments={},
            result="ok",
            status="success",
            duration_ms=0,
        )

        timestamp = datetime.fromisoformat(
            _read_events(self.root / "logs/memory.jsonl")[0]["timestamp"]
        )

        self.assertIsNotNone(timestamp.utcoffset())
        self.assertEqual(timestamp.utcoffset(), timestamp.astimezone().utcoffset())

    def test_memory_dispatch_logs_success_and_failure_results(self) -> None:
        with patch.object(memory_tools, "MEMORY_ROOT", self.root / "memory"):
            success = memory_tools.dispatch(
                "update_person_summary",
                {"content": "first line\nsecond line\nthird line"},
            )
            failure = memory_tools.dispatch("read_memory", {"path": "missing.md"})

        events = _read_events(self.root / "logs/memory.jsonl")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["result"], success)
        self.assertEqual(events[0]["status"], "success")
        self.assertEqual(
            events[0]["arguments"]["content"],
            "first line\nsecond line\nthird line",
        )
        self.assertEqual(events[1]["result"], failure)
        self.assertEqual(events[1]["status"], "error")

    def test_app_dispatch_logs_to_its_own_file_with_tool_result(self) -> None:
        context = app_tools.ToolContext()
        selection_result = app_tools.dispatch_tool(
            "select_app", {"app_id": "calendar"}, context
        )
        tool_result = "Created event with confirmed storage result."
        with patch.object(
            app_tools.APP_TOOLSETS["calendar"],
            "dispatch",
            return_value=tool_result,
        ):
            result = app_tools.dispatch_tool(
                "create_calendar_event",
                {"title": "Plan", "notes": "first line\nsecond line\nthird line"},
                context,
            )

        with patch.object(
            app_tools.APP_TOOLSETS["calendar"],
            "dispatch",
            return_value="error: calendar storage is unavailable",
        ):
            error_result = app_tools.dispatch_tool(
                "list_calendar_events", {}, context
            )

        self.assertEqual(result, tool_result)
        router_events = _read_events(self.root / "logs/apps/app_router.jsonl")
        app_events = _read_events(self.root / "logs/apps/calendar.jsonl")
        self.assertEqual(router_events[0]["result"], selection_result)
        self.assertEqual(router_events[0]["status"], "success")
        self.assertEqual(app_events[0]["tool"], "create_calendar_event")
        self.assertEqual(app_events[0]["result"], tool_result)
        self.assertEqual(app_events[0]["status"], "success")
        self.assertEqual(
            app_events[0]["arguments"]["notes"],
            "first line\nsecond line\nthird line",
        )
        self.assertEqual(app_events[1]["result"], error_result)
        self.assertEqual(app_events[1]["status"], "error")

    def test_multiline_arguments_are_truncated_by_length_not_word_count(self) -> None:
        value = "a\nb\nc\n" + ("d" * 200)

        audit_log.log_tool_call(
            app_id="memory",
            tool_name="multiline_test",
            arguments={"body_text": value},
            result="ok",
            status="success",
            duration_ms=0,
        )

        event = _read_events(self.root / "logs/memory.jsonl")[0]
        logged_value = event["arguments"]["body_text"]
        self.assertEqual(len(logged_value), 100)
        self.assertTrue(logged_value.startswith("a\nb\nc\n"))

    def test_assistant_and_user_messages_are_not_logged(self) -> None:
        with patch.object(memory_tools, "MEMORY_ROOT", self.root / "memory"):
            with patch.object(
                assistant.ollama,
                "chat",
                side_effect=[
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "persist-call-id",
                                    "function": {
                                        "name": "update_person_summary",
                                        "arguments": {
                                            "content": "a durable fact",
                                        },
                                    },
                                }
                            ],
                        }
                    },
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ASSISTANT_PRIVATE_RESPONSE",
                        }
                    },
                ],
            ):
                with patch("builtins.print"):
                    answer = assistant.run_assistant(
                        [
                            {
                                "role": "user",
                                "content": "USER_PRIVATE_MESSAGE",
                            }
                        ]
                    )

        self.assertEqual(answer, "ASSISTANT_PRIVATE_RESPONSE")
        log_text = (self.root / "logs/memory.jsonl").read_text(encoding="utf-8")
        self.assertIn("persist-call-id", log_text)
        self.assertIn("a durable fact", log_text)
        self.assertNotIn("USER_PRIVATE_MESSAGE", log_text)
        self.assertNotIn("ASSISTANT_PRIVATE_RESPONSE", log_text)

    def test_logs_rotate_and_retain_only_configured_backups(self) -> None:
        with patch.object(audit_log, "MAX_LOG_BYTES", 500):
            with patch.object(audit_log, "MAX_RECORD_BYTES", 450):
                with patch.object(audit_log, "BACKUP_COUNT", 2):
                    for number in range(6):
                        audit_log.log_tool_call(
                            app_id="memory",
                            tool_name="rotation_test",
                            arguments={"number": number},
                            result=f"result-{number}-" + ("x" * 100),
                            status="success",
                            duration_ms=1,
                        )

        active = self.root / "logs/memory.jsonl"
        self.assertTrue(active.exists())
        self.assertTrue((self.root / "logs/memory.jsonl.1").exists())
        self.assertTrue((self.root / "logs/memory.jsonl.2").exists())
        self.assertFalse((self.root / "logs/memory.jsonl.3").exists())
        self.assertLessEqual(active.stat().st_size, 500)


if __name__ == "__main__":
    unittest.main()
