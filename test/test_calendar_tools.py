import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apps import calendar_tools


class CalendarToolsTests(unittest.TestCase):
    def test_create_event_default_date_uses_server_local_date(self) -> None:
        with patch.object(
            calendar_tools.server_time,
            "local_today",
            return_value=date(2026, 11, 9),
        ):
            event = calendar_tools._normalize_event({"title": "Planning"})

        self.assertEqual(event["date"], "2026-11-09")

    def test_calendar_bridge_serializes_input_and_parses_output(self) -> None:
        default_calendar = SimpleNamespace(
            returncode=0, stdout="event-calendar-id\n", stderr=""
        )
        event_result = SimpleNamespace(
            returncode=0, stdout='{"id":"event-123"}\n', stderr=""
        )
        with patch.object(
            calendar_tools.subprocess,
            "run",
            side_effect=[default_calendar, event_result],
        ) as run:
            result = calendar_tools._run_calendar_operation(
                "read", {"event_id": "event-123"}
            )

        self.assertEqual(result, {"id": "event-123"})
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["defaults", "read", "com.apple.iCal", "defaultCalendarID"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "osascript",
                str(calendar_tools.CALENDAR_SCRIPT_PATH),
                "read",
                "event-calendar-id",
                "event-123",
            ],
        )
        self.assertTrue(run.call_args_list[0].kwargs["check"])
        self.assertTrue(run.call_args_list[1].kwargs["check"])

    def test_calendar_bridge_surfaces_automation_errors(self) -> None:
        default_calendar = SimpleNamespace(
            returncode=0, stdout="event-calendar-id", stderr=""
        )
        failure = subprocess.CalledProcessError(
            1, "osascript", stderr="Calendar access was not granted."
        )
        with patch.object(
            calendar_tools.subprocess,
            "run",
            side_effect=[default_calendar, failure],
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Calendar.app list failed: Calendar access was not granted",
            ):
                calendar_tools._run_calendar_operation("list", {})

    def test_create_event_uses_calendar_backend_and_reports_confirmed_event(self) -> None:
        event = {
            "id": "event-123",
            "title": "Planning",
            "date": "2026-11-09",
            "start_time": "09:00",
            "end_time": "10:00",
            "calendar": "Personal",
        }
        with patch.object(
            calendar_tools, "_run_calendar_operation", return_value=event
        ) as run_operation:
            result = calendar_tools.create_calendar_event(
                {"title": "Planning", "date": "2026-11-09"}
            )

        self.assertIn("Created event 'Planning' in 'Personal'", result)
        self.assertIn("event ID: event-123", result)
        run_operation.assert_called_once_with(
            "create",
            {
                "title": "Planning",
                "date": "2026-11-09",
                "start_time": "09:00",
                "end_time": "10:00",
                "notes": "",
                "reminder_minutes": 15,
            },
        )

    def test_list_events_passes_a_validated_date_range_to_calendar(self) -> None:
        events = [{"id": "event-123", "title": "Planning"}]
        with patch.object(
            calendar_tools, "_run_calendar_operation", return_value=events
        ) as run_operation:
            result = calendar_tools.list_calendar_events(
                {"start_date": "2026-11-09", "end_date": "2026-11-10"}
            )

        self.assertEqual(json.loads(result), events)
        run_operation.assert_called_once_with(
            "list",
            {"start_date": "2026-11-09", "end_date": "2026-11-10"},
        )

    def test_event_update_does_not_expose_unsupported_alert_changes(self) -> None:
        updated = {"id": "event-123", "title": "Updated"}
        with patch.object(
            calendar_tools, "_run_calendar_operation", return_value=updated
        ) as run_operation:
            result = calendar_tools.update_calendar_event(
                {"event_id": "event-123", "title": "Updated"}
            )

        self.assertEqual(result, "Updated calendar event 'Updated'")
        run_operation.assert_called_once_with(
            "update", {"event_id": "event-123", "title": "Updated"}
        )
        tools_by_name = {
            tool["function"]["name"]: tool["function"]
            for tool in calendar_tools.TOOLS
        }
        self.assertNotIn(
            "reminder_minutes",
            tools_by_name["update_calendar_event"]["parameters"]["properties"],
        )
        self.assertNotIn("add_calendar_reminder", calendar_tools.HANDLERS)

    def test_delete_requires_confirmation_before_calling_calendar(self) -> None:
        with patch.object(calendar_tools, "_run_calendar_operation") as run_operation:
            with self.assertRaisesRegex(ValueError, "confirm must be true"):
                calendar_tools.delete_calendar_event({"event_id": "event-123"})

        run_operation.assert_not_called()

    def test_native_calendar_errors_are_reported(self) -> None:
        with patch.object(
            calendar_tools,
            "_run_calendar_operation",
            side_effect=ValueError("Calendar access was not granted."),
        ):
            result = calendar_tools.dispatch(
                "list_calendar_events", {}
            )

        self.assertIn("Calendar access was not granted", result)
        self.assertTrue(result.startswith("error: list_calendar_events failed:"))

    def test_daily_progress_remains_in_local_json_and_period_summary_combines_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            progress_path = root / "daily_progress.json"
            with patch.object(calendar_tools, "MEMORY_ROOT", root):
                with patch.object(calendar_tools, "PROGRESS_PATH", progress_path):
                    calendar_tools.log_daily_progress(
                        {
                            "date": "2026-11-09",
                            "summary": "Finished the migration plan",
                            "tags": ["work"],
                        }
                    )
                    with patch.object(
                        calendar_tools,
                        "_run_calendar_operation",
                        return_value=[
                            {"id": "event-123", "date": "2026-11-09"}
                        ],
                    ) as run_operation:
                        summary = json.loads(
                            calendar_tools.summarize_calendar_period(
                                {
                                    "start_date": "2026-11-09",
                                    "end_date": "2026-11-09",
                                }
                            )
                        )

            self.assertTrue(progress_path.exists())
            self.assertEqual(summary["event_count"], 1)
            self.assertEqual(summary["daily_progress_count"], 1)
            self.assertEqual(summary["daily_progress"][0]["summary"], "Finished the migration plan")
            run_operation.assert_called_once_with(
                "list",
                {"start_date": "2026-11-09", "end_date": "2026-11-09"},
            )


if __name__ == "__main__":
    unittest.main()
