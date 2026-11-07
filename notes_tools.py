"""Apple Notes integration for a local Ollama-powered assistant.

Four tools are exposed to the LLM:

    create_note  - create a new note in the local ("On My Mac") account
    edit_note    - update the body (and optionally the title) of an existing note
    delete_note  - delete an existing note by title
    show_note    - open Notes and bring the user's attention to a note

Each tool is backed by an AppleScript snippet run via `osascript`. The
handlers return short, human/LLM-readable confirmation strings. On error
they return a descriptive string (rather than raising) so the model can
read the failure and self-correct.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

# Account name for the local (non-iCloud) Notes database. Override if your
# machine uses a different name.
ACCOUNT = "On My Mac"


# --------------------------------------------------------------------------- #
# AppleScript plumbing
# --------------------------------------------------------------------------- #
def apple_quote(value: Optional[str]) -> str:
    """Return a complete, self-contained AppleScript string-literal expression.

    Backslashes and double quotes are escaped; embedded newlines become a
    concatenation with the ``line feed`` constant, wrapped in parentheses so
    the result is a single unambiguous expression the caller can drop in
    anywhere a value is expected (no extra quotes at the call site).

    ``hello``      ->  ``"hello"``
    ``hello\\nworld`` ->  ``("hello" & line feed & "world")``
    """
    if value is None:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    segments = escaped.split("\n")
    if len(segments) == 1:
        return '"' + segments[0] + '"'
    joined = " & line feed & ".join('"' + seg + '"' for seg in segments)
    return "(" + joined + ")"


def run_applescript(script: str) -> str:
    """Run an AppleScript via osascript and return its stdout.

    Raises subprocess.CalledProcessError on non-zero exit (AppleScript
    errors). stderr is captured and attached to the exception.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise subprocess.CalledProcessError(
            returncode=124,
            cmd=["osascript", "-e", script],
            stderr="AppleScript timed out after 30s (Notes may be unresponsive).",
        )
    return result.stdout.strip()


# --------------------------------------------------------------------------- #
# AppleScript builders
# --------------------------------------------------------------------------- #
def _script_create_note(title: str, body: str) -> str:
    return f'''
    tell application "Notes"
        activate
        make new note with properties {{name:{apple_quote(title)}, body:{apple_quote(body)}}}
    end tell
'''


def _script_edit_note(title: str, body: Optional[str], new_title: Optional[str]) -> str:
    title_q = apple_quote(title)
    body_q = apple_quote(body)
        

    return f'''
tell application "Notes"
	activate
	
	set theNote to note "{title}"
	set currentBody to body of theNote
	
	-- 1. Safely extract the existing title (the first HTML block)
	set AppleScript's text item delimiters to "</div>"
	set originalTitleBlock to first text item of currentBody & "</div>"
	set AppleScript's text item delimiters to ""
	
	-- 2. Define your brand new body text
	set newContent to "<div>{body}</div>"
	
	-- 3. Piece them back together (Title block + New text)
	set body of theNote to originalTitleBlock & newContent
	
end tell
'''

def _script_list_notes() -> str:
    return f'''
tell application "Notes"
    tell folder "Notes"
        set noteTitles to name of every note
    end tell
    return noteTitles
end tell
'''


def _script_read_note(title: str) -> str:
    title_q = apple_quote(title)
    return f'''
tell application "Notes"
    tell folder "Notes"
        set noteText to plaintext of note {title_q}
    end tell
    return noteText
end tell
'''

def _script_delete_note(title: str) -> str:
    title_q = apple_quote(title)
    return f'''
tell application "Notes"
        delete (first note whose name is {title_q})
    
end tell
'''


def _script_show_note(title: str) -> str:
    title_q = apple_quote(title)
    return f'''
tell application "Notes"
    activate
    tell account "{ACCOUNT}"
        set theNote to first note whose name is {title_q}
    end tell
    if theNote is missing value then
        error "note not found: " & {title_q}
    end if
    open theNote
end tell
'''


# --------------------------------------------------------------------------- #
# Tool handlers — each takes a dict of parsed arguments, returns a string.
# --------------------------------------------------------------------------- #
def create_note(args: Dict[str, Any]) -> str:
    print(args)
    title = args.get("title") or "Untitled"
    body = args.get("body") or ""
    try:
        run_applescript(_script_create_note(title, body))
    except subprocess.CalledProcessError as e:
        return f"error: failed to create note: {e.stderr.strip() or e}"
    return f'created note "{title}" in {ACCOUNT}'


def edit_note(args: Dict[str, Any]) -> str:
    title = args.get("title")
    body = args.get("body")
    new_title = args.get("new_title")
    if not title:
        return "error: title is required for edit_note"
    try:
        run_applescript(_script_edit_note(title, body, new_title))
    except subprocess.CalledProcessError as e:
        return f'error: failed to edit note "{title}": {e.stderr.strip() or e}'
    what = "title and body" if (new_title and body is not None) else "body"
    return f'updated {what} of note "{title}"'


def delete_note(args: Dict[str, Any]) -> str:
    title = args.get("title")
    if not title:
        return "error: title is required for delete_note"
    try:
        run_applescript(_script_delete_note(title))
    except subprocess.CalledProcessError as e:
        return f'error: failed to delete note "{title}": {e.stderr.strip() or e}'
    return f'deleted note "{title}"'


def show_note(args: Dict[str, Any]) -> str:
    title = args.get("title")
    if not title:
        return "error: title is required for show_note"
    try:
        run_applescript(_script_show_note(title))
    except subprocess.CalledProcessError as e:
        return f'error: failed to show note "{title}": {e.stderr.strip() or e}'
    return f'opened note "{title}" in Notes'


# --------------------------------------------------------------------------- #
# Tool registry — OpenAI / Ollama function-calling schema
# --------------------------------------------------------------------------- #
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Create a new note in the local (On My Mac) Notes account."
            ,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title."},
                    "body": {
                        "type": "string",
                        "description": "For new lines etc, you MUST use html tags for new lines <br>, or paragraphs <p> in the body of notes.",
                    },
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_note",
            "description": (
                "Edit an existing note by title. Provide `body` to replace the "
                "body and/or `new_title` to rename. If neither is provided, the "
                "note is just opened."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the existing note to edit.",
                    },
                    "body": {
                        "type": "string",
                        "description": "New body text. Omit to leave the body unchanged."
                "For new lines etc, you MUST use html tags for new lines <br>, or paragraphs <p> in the body of notes."
                        
                    },
                    "new_title": {
                        "type": "string",
                        "description": "New title. Omit to keep the existing title.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": "Delete an existing note by title from the local account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note to delete."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_note",
            "description": "Open Apple Notes and bring the user's attention to an existing note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note to open."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List all note titles in the Notes app.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "Read the body content of a specific note by its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note to read."},
                },
                "required": ["title"],
            },
        },
    },
]

def list_notes(args: Dict[str, Any]) -> str:
    try:
        result = run_applescript(_script_list_notes())
    except subprocess.CalledProcessError as e:
        return f"error: failed to list notes: {e.stderr.strip() or e}"
    return result or "(no notes found)"


def read_note(args: Dict[str, Any]) -> str:
    title = args.get("title")
    if not title:
        return "error: title is required for read_note"
    try:
        result = run_applescript(_script_read_note(title))
    except subprocess.CalledProcessError as e:
        return f'error: failed to read note "{title}": {e.stderr.strip() or e}'
    return result or "(note is empty)"


HANDLERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "create_note": create_note,
    "edit_note": edit_note,
    "delete_note": delete_note,
    "show_note": show_note,
    "list_notes": list_notes,
    "read_note": read_note,
}



def dispatch(tool_name: str, arguments_json: str) -> str:
    """Look up a tool by name, parse its JSON args, and invoke the handler.

    `arguments_json` may be a JSON string or already a dict. Returns the
    handler's result string. Unknown tools return an error string so the
    caller can report it back to the model without crashing.
    """
    handler = HANDLERS.get(tool_name)
    if handler is None:
        return f"error: unknown tool '{tool_name}'"

    args: Any
    if isinstance(arguments_json, str):
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as e:
            return f"error: invalid JSON arguments for {tool_name}: {e}"
    else:
        args = arguments_json

    if not isinstance(args, dict):
        return f"error: arguments for {tool_name} must be a JSON object"

    try:
        return handler(args)
    except Exception as e:  # noqa: BLE001 - surface anything unexpected to the model
        return f"error: {tool_name} failed: {e}"
