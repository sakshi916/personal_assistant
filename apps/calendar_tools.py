"""Calendar and daily-planning tools for the local assistant.

The tool set is intentionally lightweight: it stores events and progress logs in
JSON files under the repository's local memory directory so the assistant can
manage a schedule without needing a cloud calendar service.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

MEMORY_ROOT = Path(__file__).resolve().parent.parent / "memory"
CALENDAR_PATH = MEMORY_ROOT / "calendar_events.json"
PROGRESS_PATH = MEMORY_ROOT / "daily_progress.json"


def _ensure_storage() -> None:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    if not CALENDAR_PATH.exists():
        CALENDAR_PATH.write_text("[]", encoding="utf-8")
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


def _required_text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _as_date(value: Any, label: str) -> str:
    if value in (None, ""):
        return date.today().isoformat()
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
    event = {
        "id": args.get("id") or uuid4().hex[:8],
        "title": title,
        "date": _as_date(args.get("date"), "date"),
        "start_time": _as_time(args.get("start_time"), "start_time", "09:00"),
        "end_time": _as_time(args.get("end_time"), "end_time", "10:00"),
        "notes": description if isinstance(description, str) else args.get("notes", ""),
        "reminder_minutes": int(args.get("reminder_minutes", 15)),
    }
    if event["notes"] is None:
        event["notes"] = ""
    if not isinstance(event["notes"], str):
        raise ValueError("notes must be text")
    if event["reminder_minutes"] < 0:
        raise ValueError("reminder_minutes must be zero or greater")
    return event


def _event_by_id(event_id: Any) -> dict[str, Any]:
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    events = _load_json(CALENDAR_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    for event in events:
        if isinstance(event, dict) and event.get("id") == event_id:
            return event
    raise ValueError(f"event not found: {event_id}")


def create_calendar_event(args: dict[str, Any]) -> str:
    event = _normalize_event(args)
    events = _load_json(CALENDAR_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    events.append(event)
    events.sort(key=lambda item: (item.get("date", ""), item.get("start_time", "00:00"), item.get("title", "")))
    _save_json(CALENDAR_PATH, events)
    return f"Created calendar event '{event['title']}' for {event['date']} at {event['start_time']}"


def list_calendar_events(args: dict[str, Any]) -> str:
    if args:
        raise ValueError("list_calendar_events does not accept arguments")
    events = _load_json(CALENDAR_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    return json.dumps(events, ensure_ascii=False)


def read_calendar_event(args: dict[str, Any]) -> str:
    event_id = args.get("event_id")
    event = _event_by_id(event_id)
    return json.dumps(event, ensure_ascii=False)


def update_calendar_event(args: dict[str, Any]) -> str:
    event_id = args.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    events = _load_json(CALENDAR_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("id") == event_id:
            updated = {**event}
            if "title" in args:
                updated["title"] = _required_text(args, "title")
            if "date" in args:
                updated["date"] = _as_date(args.get("date"), "date")
            if "start_time" in args:
                updated["start_time"] = _as_time(args.get("start_time"), "start_time")
            if "end_time" in args:
                updated["end_time"] = _as_time(args.get("end_time"), "end_time")
            if "notes" in args:
                updated["notes"] = args["notes"] if isinstance(args["notes"], str) else ""
            if "description" in args:
                updated["notes"] = args["description"] if isinstance(args["description"], str) else ""
            if "reminder_minutes" in args:
                reminder_minutes = args.get("reminder_minutes")
                if isinstance(reminder_minutes, bool) or not isinstance(reminder_minutes, int):
                    raise ValueError("reminder_minutes must be an integer")
                updated["reminder_minutes"] = max(0, reminder_minutes)
            events[index] = updated
            events.sort(key=lambda item: (item.get("date", ""), item.get("start_time", "00:00"), item.get("title", "")))
            _save_json(CALENDAR_PATH, events)
            return f"Updated calendar event '{updated['title']}'"
    raise ValueError(f"event not found: {event_id}")


def delete_calendar_event(args: dict[str, Any]) -> str:
    event_id = args.get("event_id")
    if args.get("confirm") is not True:
        raise ValueError("confirm must be true after the user explicitly confirms deletion")
    events = _load_json(CALENDAR_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    remaining = [event for event in events if not (isinstance(event, dict) and event.get("id") == event_id)]
    if len(remaining) == len(events):
        raise ValueError(f"event not found: {event_id}")
    _save_json(CALENDAR_PATH, remaining)
    return f"Deleted calendar event {event_id}"


def add_calendar_reminder(args: dict[str, Any]) -> str:
    event_id = args.get("event_id")
    reminder_minutes = args.get("reminder_minutes")
    if isinstance(reminder_minutes, bool) or not isinstance(reminder_minutes, int):
        raise ValueError("reminder_minutes must be an integer")
    event = _event_by_id(event_id)
    event["reminder_minutes"] = max(0, reminder_minutes)
    events = _load_json(CALENDAR_PATH, [])
    updated = []
    for candidate in events:
        if isinstance(candidate, dict) and candidate.get("id") == event_id:
            updated.append(event)
        else:
            updated.append(candidate)
    _save_json(CALENDAR_PATH, updated)
    return f"Set a reminder of {reminder_minutes} minutes for '{event['title']}'"


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

    events = _load_json(CALENDAR_PATH, [])
    progress = _load_json(PROGRESS_PATH, [])
    if not isinstance(events, list):
        raise ValueError("calendar events storage is corrupted")
    if not isinstance(progress, list):
        raise ValueError("daily progress storage is corrupted")

    filtered_events = [
        event for event in events
        if isinstance(event, dict)
        and start_date <= event.get("date", "") <= end_date
    ]
    filtered_logs = [
        entry for entry in progress
        if isinstance(entry, dict)
        and start_date <= entry.get("date", "") <= end_date
    ]

    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "event_count": len(filtered_events),
        "events": filtered_events,
        "daily_progress_count": len(filtered_logs),
        "daily_progress": filtered_logs,
        "headline": (
            f"During {start_date} to {end_date}, you have {len(filtered_events)} scheduled events "
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
        "Create a new calendar event with an optional reminder and notes.",
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
        "List all saved calendar events.",
        {},
        [],
    ),
    _tool(
        "read_calendar_event",
        "Read the details for a single calendar event by ID.",
        {"event_id": {"type": "string", "description": "Calendar event ID."}},
        ["event_id"],
    ),
    _tool(
        "update_calendar_event",
        "Update a saved calendar event by ID.",
        {
            "event_id": {"type": "string", "description": "Calendar event ID."},
            "title": {"type": "string", "description": "Updated event title."},
            "date": {"type": "string", "description": "Updated event date in YYYY-MM-DD format."},
            "start_time": {"type": "string", "description": "Updated start time in HH:MM format."},
            "end_time": {"type": "string", "description": "Updated end time in HH:MM format."},
            "notes": {"type": "string", "description": "Updated description or notes."},
            "reminder_minutes": {"type": "integer", "description": "Updated reminder offset in minutes."},
        },
        ["event_id"],
    ),
    _tool(
        "delete_calendar_event",
        "Delete a saved calendar event permanently after explicit confirmation.",
        {
            "event_id": {"type": "string", "description": "Calendar event ID to delete."},
            "confirm": {"type": "boolean", "description": "Must be true only after user confirmation."},
        },
        ["event_id", "confirm"],
    ),
    _tool(
        "add_calendar_reminder",
        "Add or change the reminder for a specific calendar event.",
        {
            "event_id": {"type": "string", "description": "Calendar event ID."},
            "reminder_minutes": {"type": "integer", "description": "Minutes before the event to remind the user."},
        },
        ["event_id", "reminder_minutes"],
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
        "Summarize all calendar events and daily progress entries within a date range.",
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
    "add_calendar_reminder": add_calendar_reminder,
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
