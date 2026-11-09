"""Filesystem-backed document tools for the local Obsidian vault."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

VAULT_ROOT = Path(__file__).resolve().parent.parent / "memory" / "obsidian" / "personal"
MAX_READ_CHARACTERS = 40_000
MAX_SEARCH_RESULTS = 20
_SEARCH_TERM = re.compile(r"[\w'-]+", re.UNICODE)


def _document_path(value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty vault-relative Markdown path")
    if "\\" in value or "\x00" in value:
        raise ValueError("path must use '/' separators and cannot contain NUL")

    relative = PurePosixPath(value.strip())
    if relative.is_absolute():
        raise ValueError("path must be relative to the Obsidian vault")
    if not relative.parts or any(
        part in ("", ".", "..") or part.startswith(".")
        for part in relative.parts
    ):
        raise ValueError("path cannot contain hidden, '.' or '..' segments")
    if relative.suffix.lower() != ".md":
        raise ValueError("document path must end in .md")

    root = VAULT_ROOT.resolve()
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("document path resolves outside the Obsidian vault")
    return relative.as_posix(), path


def _folder_path(value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty vault-relative folder path")
    if "\\" in value or "\x00" in value:
        raise ValueError("path must use '/' separators and cannot contain NUL")

    relative = PurePosixPath(value.strip())
    if relative.is_absolute():
        raise ValueError("path must be relative to the Obsidian vault")
    if not relative.parts or any(
        part in ("", ".", "..") or part.startswith(".")
        for part in relative.parts
    ):
        raise ValueError("folder path cannot contain hidden, '.' or '..' segments")

    root = VAULT_ROOT.resolve()
    path = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("folder path cannot include symbolic links")
    if not path.resolve().is_relative_to(root):
        raise ValueError("folder path resolves outside the Obsidian vault")
    return relative.as_posix(), path


def _required_text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else None
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        if existing_mode is not None:
            temporary_path.chmod(existing_mode)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as document:
        return document.read()


def create_document(args: dict[str, Any]) -> str:
    relative, path = _document_path(args.get("path"))
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be text")
    if path.exists():
        raise FileExistsError(f"document already exists: {relative}")
    _atomic_write(path, content)
    return f"Created Obsidian document: {relative}"


def read_document(args: dict[str, Any]) -> str:
    relative, path = _document_path(args.get("path"))
    if not path.is_file():
        raise FileNotFoundError(f"document not found: {relative}")

    content = _read_text(path)
    start = args.get("start", 0)
    end = args.get("end")
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise ValueError("start must be a non-negative character offset")
    if end is not None and (
        isinstance(end, bool) or not isinstance(end, int) or end < start
    ):
        raise ValueError("end must be an integer greater than or equal to start")

    requested_end = min(
        len(content),
        start + MAX_READ_CHARACTERS,
        len(content) if end is None else end,
    )
    excerpt = content[start:requested_end]
    result = {
        "path": relative,
        "total_characters": len(content),
        "start": min(start, len(content)),
        "end": requested_end,
        "content": excerpt,
    }
    if requested_end < len(content):
        result["truncated"] = True
        result["hint"] = (
            "Read another range with start/end to inspect the rest of the document."
        )
    return json.dumps(result, ensure_ascii=False)


def edit_document(args: dict[str, Any]) -> str:
    relative, path = _document_path(args.get("path"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str):
        raise ValueError("old_text must be text")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be text")
    if not path.is_file():
        raise FileNotFoundError(f"document not found: {relative}")

    content = _read_text(path)
    if old_text == "":
        if content:
            raise ValueError(
                "old_text cannot be empty for a non-empty document; use a unique passage"
            )
        updated = new_text
    else:
        first = content.find(old_text)
        if first < 0:
            raise ValueError("old_text was not found; reread the document before editing")
        if content.find(old_text, first + 1) >= 0:
            raise ValueError(
                "old_text is ambiguous because it occurs more than once; use a longer passage"
            )
        updated = content[:first] + new_text + content[first + len(old_text) :]

    _atomic_write(path, updated)
    return f"Updated Obsidian document: {relative}"


def delete_document(args: dict[str, Any]) -> str:
    relative, path = _document_path(args.get("path"))
    if args.get("confirm") is not True:
        raise ValueError("confirm must be true after the user explicitly confirms deletion")
    if not path.is_file():
        raise FileNotFoundError(f"document not found: {relative}")
    path.unlink()
    return f"Deleted Obsidian document: {relative}"


def delete_folder(args: dict[str, Any]) -> str:
    relative, path = _folder_path(args.get("path"))
    if args.get("confirm") is not True:
        raise ValueError(
            "confirm must be true after the user explicitly confirms deleting "
            "the folder and all its contents"
        )
    if not path.is_dir():
        raise FileNotFoundError(f"folder not found: {relative}")
    shutil.rmtree(path)
    return f"Deleted Obsidian folder and its contents: {relative}"


def show_document(args: dict[str, Any]) -> str:
    relative, path = _document_path(args.get("path"))
    if not path.is_file():
        raise FileNotFoundError(f"document not found: {relative}")

    try:
        subprocess.run(
            ["open", "-a", "Obsidian", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("timed out while opening the document in Obsidian") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or str(error)
        raise ValueError(f"could not open the document in Obsidian: {detail}") from error
    return f"Opened in Obsidian: {relative}"


def list_documents(args: dict[str, Any]) -> str:
    if args:
        raise ValueError("list_documents does not accept arguments")
    if not VAULT_ROOT.is_dir():
        raise FileNotFoundError(f"Obsidian vault not found: {VAULT_ROOT}")

    paths = sorted(
        file.relative_to(VAULT_ROOT).as_posix()
        for file in VAULT_ROOT.rglob("*.md")
        if file.is_file()
        and not any(part.startswith(".") for part in file.relative_to(VAULT_ROOT).parts)
    )
    return json.dumps(paths, ensure_ascii=False)


def search_documents(args: dict[str, Any]) -> str:
    query = _required_text(args, "query").casefold()
    limit = args.get("limit", 10)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_SEARCH_RESULTS
    ):
        raise ValueError(f"limit must be an integer from 1 to {MAX_SEARCH_RESULTS}")
    if not VAULT_ROOT.is_dir():
        raise FileNotFoundError(f"Obsidian vault not found: {VAULT_ROOT}")

    terms = set(_SEARCH_TERM.findall(query))
    matches: list[tuple[int, str, int, str]] = []
    for file in VAULT_ROOT.rglob("*.md"):
        relative = file.relative_to(VAULT_ROOT).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            continue
        relative, path = _document_path(relative)
        if not path.is_file():
            continue
        content = _read_text(path)
        folded = content.casefold()
        score = sum(folded.count(term) for term in terms)
        if not score:
            continue
        first_match = min(folded.find(term) for term in terms if term in folded)
        excerpt_start = max(0, first_match - 180)
        excerpt_end = min(len(content), first_match + 320)
        excerpt = content[excerpt_start:excerpt_end]
        if excerpt_start:
            excerpt = "..." + excerpt
        if excerpt_end < len(content):
            excerpt += "..."
        matches.append((score, relative, first_match, excerpt))

    matches.sort(key=lambda result: (-result[0], result[1].casefold()))
    return json.dumps(
        [
            {"path": relative, "match_offset": offset, "excerpt": excerpt}
            for _, relative, offset, excerpt in matches[:limit]
        ],
        ensure_ascii=False,
    )


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


_PATH_PROPERTY = {
    "path": {
        "type": "string",
        "description": "Markdown document path relative to the Obsidian vault, such as 'Stories/novel.md'.",
    }
}

TOOLS: list[dict[str, Any]] = [
    _tool(
        "create_obsidian_document",
        "Create a new Markdown document in the Obsidian vault. Existing documents are never overwritten.",
        {
            **_PATH_PROPERTY,
            "content": {"type": "string", "description": "Initial Markdown content."},
        },
        ["path", "content"],
    ),
    _tool(
        "read_obsidian_document",
        "Read a Markdown document. Large documents can be read in character ranges using optional 0-based start and exclusive end offsets.",
        {
            **_PATH_PROPERTY,
            "start": {"type": "integer", "description": "Optional 0-based character offset to begin reading."},
            "end": {"type": "integer", "description": "Optional exclusive character offset to stop reading."},
        },
        ["path"],
    ),
    _tool(
        "edit_obsidian_document",
        "Replace one exact, unique passage in an existing Markdown document. Read the document first; if a passage occurs more than once, use a longer unique passage. This edits the file directly rather than using character offsets.",
        {
            **_PATH_PROPERTY,
            "old_text": {"type": "string", "description": "Exact existing passage to replace; must match uniquely."},
            "new_text": {"type": "string", "description": "Replacement passage."},
        },
        ["path", "old_text", "new_text"],
    ),
    _tool(
        "delete_obsidian_document",
        "Permanently delete a Markdown document. Ask the user for explicit confirmation before calling this tool and set confirm to true only after confirmation.",
        {
            **_PATH_PROPERTY,
            "confirm": {"type": "boolean", "description": "Must be true only after the user explicitly confirms permanent deletion."},
        },
        ["path", "confirm"],
    ),
    _tool(
        "delete_obsidian_folder",
        "Permanently delete a folder and all files and subfolders inside it. Ask the user for explicit confirmation of the folder path and recursive deletion before calling this tool and set confirm to true only after confirmation. The vault root and hidden Obsidian configuration are protected.",
        {
            "path": {
                "type": "string",
                "description": "Folder path relative to the Obsidian vault; cannot be the vault root or a hidden folder.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true only after the user explicitly confirms deleting this folder and all its contents.",
            },
        },
        ["path", "confirm"],
    ),
    _tool(
        "show_obsidian_document",
        "Open a Markdown document in Obsidian so the user can view it and observe requested edits.",
        _PATH_PROPERTY,
        ["path"],
    ),
    _tool(
        "list_obsidian_documents",
        "List Markdown documents in the Obsidian vault, excluding hidden Obsidian configuration files.",
        {},
        [],
    ),
    _tool(
        "search_obsidian_documents",
        "Search Markdown document contents in the Obsidian vault and return matching paths, offsets, and excerpts.",
        {
            "query": {"type": "string", "description": "Words or phrases to search for."},
            "limit": {"type": "integer", "description": "Maximum matches to return (1-20, default 10)."},
        },
        ["query"],
    ),
]

HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "create_obsidian_document": create_document,
    "read_obsidian_document": read_document,
    "edit_obsidian_document": edit_document,
    "delete_obsidian_document": delete_document,
    "delete_obsidian_folder": delete_folder,
    "show_obsidian_document": show_document,
    "list_obsidian_documents": list_documents,
    "search_obsidian_documents": search_documents,
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
