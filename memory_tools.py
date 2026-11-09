"""Filesystem-backed memory tools for future assistant capabilities.

These tools are deliberately independent of the current Notes workflow.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import audit_log
import server_time

MEMORY_ROOT = Path(__file__).resolve().parent / "memory"
INDEX_NAME = "memory_index.json"
PERSON_SUMMARY_NAME = "personSummary.txt"
MAX_HISTORY_MESSAGES = 20
MAX_READ_CHARACTERS = 20_000
MAX_RETRIEVAL_RESULTS = 10
_PATH_SEGMENT = re.compile(r"^[\w][\w .+-]{0,63}$", re.UNICODE)


def _validate_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise ValueError(f"{label} must use '/' separators")

    path = PurePosixPath(value.strip())
    if path.is_absolute():
        raise ValueError(f"{label} must be relative to the memory folder")

    for part in path.parts:
        if (
            part in ("", ".", "..")
            or part.startswith(".")
            or part.endswith((" ", "."))
            or not _PATH_SEGMENT.fullmatch(part)
        ):
            raise ValueError(f"invalid {label} segment: {part!r}")

    return path.as_posix()


def _memory_path(relative_path: str) -> Path:
    root = MEMORY_ROOT.resolve()
    result = (root / relative_path).resolve()
    if not result.is_relative_to(root):
        raise ValueError("memory path resolves outside the memory folder")
    return result


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_index() -> dict[str, Any]:
    path = _memory_path(INDEX_NAME)
    if not path.exists():
        return {"version": 1, "entries": []}

    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"memory index contains invalid JSON: {error}") from error

    if (
        not isinstance(index, dict)
        or not isinstance(index.get("entries"), list)
        or index.get("version") != 1
    ):
        raise ValueError("memory index must be a version 1 object with an entries list")

    for entry in index["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("memory index entries must be objects")
        entry["path"] = _validate_relative_path(entry.get("path"), "index path")
        for field in ("category", "topic", "description", "updated_at"):
            if not isinstance(entry.get(field), str):
                raise ValueError(f"memory index entry field {field!r} must be text")
        _memory_path(entry["path"])

    return index


def _save_index_entry(
    relative_path: str, category: str, topic: str, description: str
) -> None:
    index = _load_index()
    updated_at = server_time.local_now().isoformat()
    new_entry = {
        "path": relative_path,
        "category": category,
        "topic": topic,
        "description": description,
        "updated_at": updated_at,
    }
    entries = [entry for entry in index["entries"] if entry["path"] != relative_path]
    entries.append(new_entry)
    index["entries"] = sorted(entries, key=lambda entry: entry["path"].casefold())
    _atomic_write(
        _memory_path(INDEX_NAME),
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    )


def _required_text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _validate_category(value: Any) -> str:
    category = _validate_relative_path(value, "category")
    if PurePosixPath(category).parts[0] in {
        INDEX_NAME,
        PERSON_SUMMARY_NAME,
        "README.md",
    }:
        raise ValueError("category conflicts with a reserved memory-root file")
    return category


def read_memory(args: dict[str, Any]) -> str:
    """Read a file in the memory folder using its relative path."""
    relative_path = _validate_relative_path(args.get("path"), "path")
    path = _memory_path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"memory file not found: {relative_path}")
    content = path.read_text(encoding="utf-8")
    if len(content) > MAX_READ_CHARACTERS:
        content = content[:MAX_READ_CHARACTERS] + "\n[truncated]"
    return content or "(memory file is empty)"


def retrieve_memory(args: dict[str, Any]) -> str:
    """Search indexed memory by query terms and return the most relevant entries."""
    query = _required_text(args, "query").casefold()
    limit = args.get("limit", 5)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RETRIEVAL_RESULTS
    ):
        raise ValueError(f"limit must be an integer from 1 to {MAX_RETRIEVAL_RESULTS}")

    index = _load_index()
    terms = set(re.findall(r"[\w.+-]+", query, flags=re.UNICODE))
    matches: list[tuple[int, dict[str, str], str]] = []
    for entry in index["entries"]:
        path = _memory_path(entry["path"])
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        searchable = " ".join(
            (entry["category"], entry["topic"], entry["description"], content)
        ).casefold()
        score = sum(1 for term in terms if term in searchable)
        if score:
            excerpt = content[:1000]
            if len(content) > 1000:
                excerpt += "\n[truncated]"
            matches.append((score, entry, excerpt))

    matches.sort(key=lambda result: (-result[0], result[1]["path"].casefold()))
    if not matches:
        return "No matching memory found."

    return json.dumps(
        [
            {
                "path": entry["path"],
                "category": entry["category"],
                "topic": entry["topic"],
                "description": entry["description"],
                "excerpt": excerpt,
            }
            for _, entry, excerpt in matches[:limit]
        ],
        ensure_ascii=False,
        indent=2,
    )


def update_memory(args: dict[str, Any]) -> str:
    """Create or replace a durable topic memory and register it in the index."""
    category = _validate_category(args.get("category"))
    topic = _validate_relative_path(args.get("topic"), "topic")
    content = _required_text(args, "content")
    description = _required_text(args, "description")
    relative_path = f"{category}/{topic}.md"
    path = _memory_path(relative_path)
    _atomic_write(path, content.rstrip() + "\n")
    _save_index_entry(relative_path, category, topic, description)
    return f"Updated memory: {relative_path}"


def update_person_summary(args: dict[str, Any]) -> str:
    """Create or replace the root-level person summary and index it."""
    content = _required_text(args, "content")
    description = args.get(
        "description", "General profile, preferences, goals, and behavior."
    )
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be non-empty text")

    path = _memory_path(PERSON_SUMMARY_NAME)
    _atomic_write(path, content.rstrip() + "\n")
    _save_index_entry(PERSON_SUMMARY_NAME, "person", "summary", description.strip())
    return f"Updated memory: {PERSON_SUMMARY_NAME}"


def append_recent_history(args: dict[str, Any]) -> str:
    """Append one message to a category's recent history, retaining 20 entries."""
    category = _validate_category(args.get("category"))
    role = _required_text(args, "role")
    if role not in {"user", "assistant"}:
        raise ValueError("role must be 'user' or 'assistant'")
    content = _required_text(args, "content")

    relative_path = f"{category}/recentHistory.json"
    path = _memory_path(relative_path)
    if path.exists():
        try:
            messages = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{relative_path} contains invalid JSON: {error}") from error
        if not isinstance(messages, list):
            raise ValueError(f"{relative_path} must contain a JSON array")
    else:
        messages = []

    messages.append(
        {
            "role": role,
            "content": content,
            "timestamp": server_time.local_now().isoformat(),
        }
    )
    messages = messages[-MAX_HISTORY_MESSAGES:]
    _atomic_write(path, json.dumps(messages, ensure_ascii=False, indent=2) + "\n")
    _save_index_entry(
        relative_path,
        category,
        "recent history",
        f"Recent conversation messages for the {category} category.",
    )
    return f"Appended message to {relative_path} ({len(messages)} messages retained)"


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
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
        "retrieve_memory",
        "Search the memory index for relevant profile or topic memories.",
        {
            "query": {"type": "string", "description": "Terms describing the memory to find."},
            "limit": {"type": "integer", "description": "Maximum results (1-10, default 5)."},
        },
        ["query"],
    ),
    _tool(
        "read_memory",
        "Read a memory file by its path relative to the memory folder.",
        {"path": {"type": "string", "description": "For example personSummary.txt or tech/dsa/recentHistory.json."}},
        ["path"],
    ),
    _tool(
        "update_memory",
        "Save or revise an important, durable fact or meaningful progress likely to help in future sessions. Reuse an existing category; create a category only for a distinct, ongoing topic. Do not save routine messages or sessions; if unsure, do not save.",
        {
            "category": {"type": "string", "description": "Category path, such as tech or tech/languages."},
            "topic": {"type": "string", "description": "Topic path within the category, such as dsa/progress."},
            "content": {"type": "string", "description": "Markdown content for the durable memory."},
            "description": {"type": "string", "description": "Short searchable description stored in the memory index."},
        },
        ["category", "topic", "content", "description"],
    ),
    _tool(
        "update_person_summary",
        "Replace personSummary.txt only for an important, durable personal fact or meaningful progress—not every message or session. Read the latest file first with read_memory(path='personSummary.txt') and preserve relevant facts.",
        {
            "content": {
                "type": "string",
                "description": "The complete revised summary, preserving relevant existing facts from the latest personSummary.txt.",
            },
            "description": {"type": "string", "description": "Optional searchable description."},
        },
        ["content"],
    ),
    _tool(
        "append_recent_history",
        "Append a message only when it captures important context or progress useful to future work; do not log every exchange or session. Categories are created as needed, so use an existing category unless a distinct ongoing topic warrants a new one. Retains at most 20 messages.",
        {
            "category": {"type": "string", "description": "Category path, created if needed."},
            "role": {"type": "string", "enum": ["user", "assistant"]},
            "content": {"type": "string", "description": "Message text."},
        },
        ["category", "role", "content"],
    ),
]

HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "retrieve_memory": retrieve_memory,
    "read_memory": read_memory,
    "update_memory": update_memory,
    "update_person_summary": update_person_summary,
    "append_recent_history": append_recent_history,
}

def dispatch(
    tool_name: str,
    arguments_json: str | dict[str, Any],
) -> str:
    """Parse tool arguments, invoke a memory handler, and surface expected errors."""
    started_at = audit_log.start_timer()
    handler = HANDLERS.get(tool_name)
    if handler is None:
        result = f"error: unknown tool {tool_name!r}"
        audit_log.log_tool_call(
            app_id="memory",
            tool_name=tool_name,
            arguments=arguments_json,
            result=result,
            status="error",
            duration_ms=audit_log.elapsed_ms(started_at),
        )
        return result

    try:
        args = json.loads(arguments_json) if isinstance(arguments_json, str) else arguments_json
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be a JSON object")
        result = handler(args)
    except json.JSONDecodeError as error:
        result = f"error: invalid JSON arguments for {tool_name}: {error}"
    except (OSError, ValueError) as error:
        result = f"error: {tool_name} failed: {error}"
    except Exception as error:
        result = f"error: {tool_name} failed: {error}"
        audit_log.log_tool_call(
            app_id="memory",
            tool_name=tool_name,
            arguments=arguments_json,
            result=result,
            status="error",
            duration_ms=audit_log.elapsed_ms(started_at),
        )
        raise

    status = "error" if result.startswith("error:") else "success"
    audit_log.log_tool_call(
        app_id="memory",
        tool_name=tool_name,
        arguments=arguments_json,
        result=result,
        status=status,
        duration_ms=audit_log.elapsed_ms(started_at),
    )
    return result
