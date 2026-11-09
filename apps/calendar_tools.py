"""macOS Calendar integration and local daily-planning tools."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import server_time

MEMORY_ROOT = Path(__file__).resolve().parent.parent / "memory"
PROGRESS_PATH = MEMORY_ROOT / "daily_progress.json"
CALENDAR_SCRIPT_PATH = Path(__file__).with_name("calendar.applescript")


def _ensure_storage() -> None:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    if not PROGRESS_PATH.exists():
        PROGRESS_PATH.write_text("[]", encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    _ensure_storage()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    if not raw.strip():
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path.name}: {exc}") from exc
    return value if value is not None else default


def _save_json(path: Path, value: Any) -> None:
    _ensure_storage()
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _default_calendar_id() -> str:
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.iCal", "defaultCalendarID"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("Timed out reading the macOS default Calendar") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "default calendar preference is unavailable"
        raise ValueError(
            f"Could not determine the macOS default calendar: {detail}"
        ) from error

    calendar_id = result.stdout.strip()
    if not calendar_id:
        raise ValueError("macOS does not have a default Calendar configured")
    return calendar_id


def _calendar_arguments(operation: str, args: dict[str, Any]) -> list[str]:
    if operation == "create":
        return [
            args["title"],
            args["date"],
            args["start_time"],
            args["end_time"],
            args["notes"],
            str(args["reminder_minutes"]),
        ]
    if operation == "list":
        return [args.get("start_date", ""), args.get("end_date", "")]
    if operation in {"read", "delete"}:
        return [args["event_id"]]
    if operation == "reminder":
        return [args["event_id"], str(args["reminder_minutes"])]
    if operation == "update":
        return [
            args["event_id"],
            args.get("title", ""),
            args.get("date", ""),
            args.get("start_time", ""),
            args.get("end_time", ""),
            args.get("notes", ""),
            "1" if "notes" in args else "0",
        ]
    raise ValueError(f"unsupported Calendar operation: {operation}")


def _run_calendar_operation(operation: str, args: dict[str, Any]) -> Any:
    calendar_id = _default_calendar_id()
    command = [
        "osascript",
        str(CALENDAR_SCRIPT_PATH),
        operation,
        calendar_id,
        *_calendar_arguments(operation, args),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"Calendar.app {operation} operation timed out") from error
    except subprocess.CalledProcessError as error:
        details = error.stderr.strip() or f"osascript exited with status {error.returncode}"
        raise ValueError(f"Calendar.app {operation} failed: {details}") from error

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Calendar.app {operation} returned invalid JSON: {error}"
        ) from error


def _required_text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _as_date(value: Any, label: str) -> str:
    if value in (None, ""):
        return server_time.local_today().isoformat()
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be a YYYY-MM-DD string") from exc


def _as_time(value: Any, label: str, fallback: str = "09:00") -> str:
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a HH:MM time string")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{label} must be a HH:MM time string") from exc
    return parsed.strftime("%H:%M")


def _normalize_event(args: dict[str, Any]) -> dict[str, Any]:
    description = args.get("description")
    title = _required_text(args, "title")
    reminder_minutes = args.get("reminder_minutes", 15)
    if isinstance(reminder_minutes, bool) or not isinstance(reminder_minutes, int):
        raise ValueError("reminder_minutes must be an integer")
    if reminder_minutes < 0:
        raise ValueError("reminder_minutes must be zero or greater")

    event = {
        "title": title,
        "date": _as_date(args.get("date"), "date"),
        "start_time": _as_time(args.get("start_time"), "start_time", "09:00"),
        "end_time": _as_time(args.get("end_time"), "end_time", "10:00"),
        "notes": description if isinstance(description, str) else args.get("notes", ""),
        "reminder_minutes": reminder_minutes,
    }
    if event["notes"] is None:
        event["notes"] = ""
    if not isinstance(event["notes"], str):
        raise ValueError("notes must be text")
    if event["end_time"] <= event["start_time"]:
        raise ValueError("end_time must be after start_time")
    return event


def create_calendar_event(args: dict[str, Any]) -> str:
    event = _normalize_event(args)
    saved = _run_calendar_operation("create", event)
    if not isinstance(saved, dict) or not saved.get("id"):
        raise ValueError("Calendar.app did not return the saved event identifier")
    return (
        f"Created event '{saved.get('title', event['title'])}' in "
        f"'{saved.get('calendar', 'the default calendar')}' for "
        f"{saved.get('date', event['date'])} at "
        f"{saved.get('start_time', event['start_time'])} "
        f"(event ID: {saved['id']})"
    )


def list_calendar_events(args: dict[str, Any]) -> str:
    unexpected = set(args) - {"start_date", "end_date"}
    if unexpected:
        raise ValueError(
            f"unsupported list_calendar_events argument(s): {', '.join(sorted(unexpected))}"
        )
    if bool(args.get("start_date")) != bool(args.get("end_date")):
        raise ValueError("start_date and end_date must be provided together")

    query: dict[str, Any] = {}
    if args.get("start_date") and args.get("end_date"):
        query["start_date"] = _as_date(args["start_date"], "start_date")
        query["end_date"] = _as_date(args["end_date"], "end_date")
        if query["end_date"] < query["start_date"]:
            raise ValueError("end_date must be on or after start_date")

    events = _run_calendar_operation("list", query)
    if not isinstance(events, list):
        raise ValueError("Calendar.app returned an invalid event list")
    return json.dumps(events, ensure_ascii=False)


def read_calendar_event(args: dict[str, Any]) -> str:
    event_id = _required_text(args, "event_id")
    event = _run_calendar_operation("read", {"event_id": event_id})
    if not isinstance(event, dict):
        raise ValueError("Calendar.app returned an invalid event")
    return json.dumps(event, ensure_ascii=False)


def update_calendar_event(args: dict[str, Any]) -> str:
    event_id = _required_text(args, "event_id")
    updates: dict[str, Any] = {"event_id": event_id}
    if "title" in args:
        updates["title"] = _required_text(args, "title")
    if "date" in args:
        updates["date"] = _as_date(args["date"], "date")
    if "start_time" in args:
        updates["start_time"] = _as_time(args["start_time"], "start_time")
    if "end_time" in args:
        updates["end_time"] = _as_time(args["end_time"], "end_time")
    if "notes" in args or "description" in args:
        notes = args.get("description", args.get("notes", ""))
        if not isinstance(notes, str):
            raise ValueError("notes must be text")
        updates["notes"] = notes
    updated = _run_calendar_operation("update", updates)
    if not isinstance(updated, dict):
        raise ValueError("Calendar.app returned an invalid updated event")
    return f"Updated calendar event '{updated.get('title', event_id)}'"


def delete_calendar_event(args: dict[str, Any]) -> str:
    event_id = _required_text(args, "event_id")
    if args.get("confirm") is not True:
        raise ValueError("confirm must be true after the user explicitly confirms deletion")
    _run_calendar_operation("delete", {"event_id": event_id})
    return f"Deleted calendar event {event_id}"


def log_daily_progress(args: dict[str, Any]) -> str:
    date_value = _as_date(args.get("date"), "date")
    summary = _required_text(args, "summary")
    log = {
        "date": date_value,
        "summary": summary,
        "notes": args.get("notes", ""),
        "tags": args.get("tags", []),
    }
    if not isinstance(log["notes"], str):
        raise ValueError("notes must be text")
    if not isinstance(log["tags"], list):
        raise ValueError("tags must be a list of strings")
    if any(not isinstance(tag, str) for tag in log["tags"]):
        raise ValueError("tags must be a list of strings")

    entries = _load_json(PROGRESS_PATH, [])
    if not isinstance(entries, list):
        raise ValueError("daily progress storage is corrupted")
    entries.append(log)
    _save_json(PROGRESS_PATH, entries)
    return f"Logged daily progress for {date_value}"


def summarize_calendar_period(args: dict[str, Any]) -> str:
    start_date = _as_date(args.get("start_date"), "start_date")
    end_date = _as_date(args.get("end_date"), "end_date")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    events = json.loads(
        list_calendar_events({"start_date": start_date, "end_date": end_date})
    )
    progress = _load_json(PROGRESS_PATH, [])
    if not isinstance(progress, list):
        raise ValueError("daily progress storage is corrupted")

    filtered_logs = [
        entry for entry in progress
        if isinstance(entry, dict)
        and start_date <= entry.get("date", "") <= end_date
    ]

    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "event_count": len(events),
        "events": events,
        "daily_progress_count": len(filtered_logs),
        "daily_progress": filtered_logs,
        "headline": (
            f"During {start_date} to {end_date}, you have {len(events)} scheduled events "
            f"and {len(filtered_logs)} daily progress entries."
        ),
    }
    return json.dumps(summary, ensure_ascii=False)


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "create_calendar_event",
        "Create an event in macOS Calendar's default calendar with a native "
        "display alert and optional notes.",
        {
            "title": {"type": "string", "description": "Short event title."},
            "date": {"type": "string", "description": "Event date in YYYY-MM-DD format."},
            "start_time": {"type": "string", "description": "Event start time in HH:MM 24-hour format."},
            "end_time": {"type": "string", "description": "Event end time in HH:MM 24-hour format."},
            "notes": {"type": "string", "description": "Optional notes for the event."},
            "reminder_minutes": {"type": "integer", "description": "Minutes before the event to remind the user."},
        },
        ["title"],
    ),
    _tool(
        "list_calendar_events",
        "List events from writable macOS Calendar calendars. Optionally filter by an inclusive "
        "start_date and end_date range.",
        {
            "start_date": {"type": "string", "description": "Optional range start date in YYYY-MM-DD format; requires end_date."},
            "end_date": {"type": "string", "description": "Optional inclusive range end date in YYYY-MM-DD format; requires start_date."},
        },
        [],
    ),
    _tool(
        "read_calendar_event",
        "Read the details for a single macOS Calendar event by its event ID.",
        {"event_id": {"type": "string", "description": "Calendar.app event ID returned by list_calendar_events or create_calendar_event."}},
        ["event_id"],
    ),
    _tool(
        "update_calendar_event",
        "Update a macOS Calendar event's title, date, time, or notes by event ID. "
        "Alerts cannot be changed after event creation.",
        {
            "event_id": {"type": "string", "description": "Calendar event ID."},
            "title": {"type": "string", "description": "Updated event title."},
            "date": {"type": "string", "description": "Updated event date in YYYY-MM-DD format."},
            "start_time": {"type": "string", "description": "Updated start time in HH:MM format."},
            "end_time": {"type": "string", "description": "Updated end time in HH:MM format."},
            "notes": {"type": "string", "description": "Updated description or notes."},
        },
        ["event_id"],
    ),
    _tool(
        "delete_calendar_event",
        "Delete a macOS Calendar event permanently after explicit confirmation.",
        {
            "event_id": {"type": "string", "description": "Calendar event ID to delete."},
            "confirm": {"type": "boolean", "description": "Must be true only after user confirmation."},
        },
        ["event_id", "confirm"],
    ),
    _tool(
        "log_daily_progress",
        "Record a daily progress update for a date; used for later summaries.",
        {
            "date": {"type": "string", "description": "Progress date in YYYY-MM-DD format."},
            "summary": {"type": "string", "description": "A short summary of what was accomplished."},
            "notes": {"type": "string", "description": "Optional extra notes or context."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for the update."},
        },
        ["summary"],
    ),
    _tool(
        "summarize_calendar_period",
        "Summarize macOS Calendar events and locally stored daily progress "
        "entries within a date range.",
        {
            "start_date": {"type": "string", "description": "Range start date in YYYY-MM-DD format."},
            "end_date": {"type": "string", "description": "Range end date in YYYY-MM-DD format."},
        },
        ["start_date", "end_date"],
    ),
]

HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "create_calendar_event": create_calendar_event,
    "list_calendar_events": list_calendar_events,
    "read_calendar_event": read_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event,
    "log_daily_progress": log_daily_progress,
    "summarize_calendar_period": summarize_calendar_period,
}


def dispatch(tool_name: str, arguments_json: str | dict[str, Any]) -> str:
    """Parse tool arguments, invoke a handler, and return model-readable results."""
    handler = HANDLERS.get(tool_name)
    if handler is None:
        return f"error: unknown tool {tool_name!r}"

    try:
        args = (
            json.loads(arguments_json)
            if isinstance(arguments_json, str)
            else arguments_json
        )
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be a JSON object")
        return handler(args)
    except json.JSONDecodeError as error:
        return f"error: invalid JSON arguments for {tool_name}: {error}"
    except (OSError, ValueError) as error:
        return f"error: {tool_name} failed: {error}"
