"""Local, rotated JSONL audit logs for memory and application tool calls."""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import server_time

LOG_ROOT = Path(__file__).resolve().parent / "logs"
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
MAX_RECORD_BYTES = 9 * 1024 * 1024
MAX_ARGUMENT_CHARACTERS = 100
MAX_ARGUMENTS_BYTES = 1024 * 1024

_lock = threading.Lock()
_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_turn_id", default=None
)
_call_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_call_id", default=None
)


@contextmanager
def assistant_turn() -> Iterator[str]:
    """Set an in-process turn ID, without persisting the conversation."""
    identifier = uuid4().hex
    token = _turn_id.set(identifier)
    try:
        yield identifier
    finally:
        _turn_id.reset(token)


@contextmanager
def tool_call(identifier: Any = None) -> Iterator[str]:
    """Set an in-process tool-call ID for correlation in the operation log."""
    call_id = identifier if isinstance(identifier, str) and identifier else uuid4().hex
    token = _call_id.set(call_id)
    try:
        yield call_id
    finally:
        _call_id.reset(token)


def _sanitize_string(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_ARGUMENT_CHARACTERS:
        return value, False
    marker = "[truncated]"
    return value[: MAX_ARGUMENT_CHARACTERS - len(marker)] + marker, True


def _sanitize_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        truncated = False
        for key, item in value.items():
            safe_key, key_was_truncated = _sanitize_string(str(key))
            safe_item, item_was_truncated = _sanitize_value(item)
            sanitized[safe_key] = safe_item
            truncated = truncated or key_was_truncated or item_was_truncated
        return sanitized, truncated
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        truncated = False
        for item in value:
            safe_item, item_was_truncated = _sanitize_value(item)
            sanitized_items.append(safe_item)
            truncated = truncated or item_was_truncated
        return sanitized_items, truncated
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    safe_value, was_truncated = _sanitize_string(str(value))
    return safe_value, was_truncated


def _sanitized_arguments(arguments: Any) -> tuple[Any, bool]:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return _sanitize_string(arguments)
        return _sanitize_value(parsed)
    return _sanitize_value(arguments)


def _record_path(app_id: str) -> Path:
    if app_id == "memory":
        return LOG_ROOT / "memory.jsonl"
    if app_id == "app_router":
        return LOG_ROOT / "apps" / "app_router.jsonl"
    if not app_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in app_id):
        raise ValueError(f"invalid audit log application ID: {app_id!r}")
    return LOG_ROOT / "apps" / f"{app_id}.jsonl"


def _rotate(path: Path) -> None:
    oldest = path.with_name(f"{path.name}.{BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for number in range(BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{number}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{number + 1}"))
    if path.exists():
        path.replace(path.with_name(f"{path.name}.1"))


def _fit_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) <= MAX_RECORD_BYTES:
        return record

    record["arguments"] = {"_truncated": "argument payload exceeded log size limit"}
    record["arguments_truncated"] = True
    result = record.get("result")
    if isinstance(result, str):
        result_bytes = result.encode("utf-8")
        allowed_bytes = max(0, MAX_RECORD_BYTES // 2)
        record["result"] = result_bytes[:allowed_bytes].decode("utf-8", errors="ignore")
        record["result_truncated"] = True

    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_RECORD_BYTES:
        record["result"] = "[result omitted: audit record exceeded size limit]"
    return record


def _append(path: Path, record: dict[str, Any]) -> None:
    safe_record = _fit_record(record)
    line = (
        json.dumps(safe_record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(line) > MAX_LOG_BYTES:
            _rotate(path)
        with path.open("ab") as log_file:
            log_file.write(line)
            log_file.flush()
            os.fsync(log_file.fileno())


def log_tool_call(
    *,
    app_id: str,
    tool_name: str,
    arguments: Any,
    result: str,
    status: str,
    duration_ms: float,
) -> None:
    """Persist one tool invocation, its bounded arguments, and its result."""
    if status not in {"success", "error"}:
        raise ValueError("tool-call status must be 'success' or 'error'")

    safe_arguments, arguments_truncated = _sanitized_arguments(arguments)
    arguments_json = json.dumps(safe_arguments, ensure_ascii=False).encode("utf-8")
    if len(arguments_json) > MAX_ARGUMENTS_BYTES:
        safe_arguments = {
            "_truncated": "argument payload exceeded audit argument size limit"
        }
        arguments_truncated = True

    record: dict[str, Any] = {
        "timestamp": server_time.local_now().isoformat(),
        "event_id": uuid4().hex,
        "turn_id": _turn_id.get(),
        "call_id": _call_id.get() or uuid4().hex,
        "app_id": app_id,
        "tool": tool_name,
        "arguments": safe_arguments,
        "arguments_truncated": arguments_truncated,
        "result": result,
        "status": status,
        "duration_ms": round(max(0.0, duration_ms), 3),
    }
    _append(_record_path(app_id), record)


def start_timer() -> float:
    """Return a monotonic timestamp for tool-duration measurement."""
    return time.perf_counter()


def elapsed_ms(started_at: float) -> float:
    """Return elapsed milliseconds from a timestamp created by start_timer()."""
    return (time.perf_counter() - started_at) * 1000
