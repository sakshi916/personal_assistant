AI Powered Personal assistant for Sakshi in MacOS.

Current capabilities:
1. Apple Notes manager - CRUD
2. Obsidian Markdown vault manager - create, read, exact-passage edit, search,
   list, show, and confirmed document/folder deletion

The Obsidian vault is stored locally at `memory/obsidian/personal`. Document
paths given to the assistant are relative to that vault. Edits replace an exact
passage that must occur only once, rather than relying on fragile character
offsets or rewriting the whole document. Folder deletion is recursive and
requires explicit confirmation; the vault root and hidden configuration are
protected.

For each request, the assistant first sees the app catalog in
[app_registry.json](app_registry.json) and selects one app. It then receives
only that app's tools for the remainder of the request, preventing Notes and
Obsidian operations from being mixed in the same tool round. Requests that
clearly require multiple apps should be handled separately.

Current capabilities:
3. Calendar scheduling, reminders, daily progress logging, and date-range summaries

Pending capabilities:
1. Browser handling capabilities

Memory tools are available in every assistant turn. At the start of each typed
or voice session, `memory/personSummary.txt` is included in the system prompt;
other indexed memories can be retrieved on demand. See
[memory/README.md](memory/README.md) for details.

During voice replies, press Command+L to stop narration and continue listening.
macOS may require Accessibility permission for the terminal or app running the
assistant to detect the key.

Memory and app tool calls are audited locally under `logs/`. Each memory/app
log records tool inputs (with string arguments capped at 100 characters) and
tool results; user and assistant chat messages are not logged.
Because tool results and non-text arguments may contain personal data, treat
these local logs as sensitive. Logs rotate at 10 MiB and retain five backups.
See [memory/README.md](memory/README.md) for paths and inspection details.