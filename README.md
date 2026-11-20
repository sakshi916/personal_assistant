AI Powered Personal assistant for Sakshi in MacOS.

## macOS app

The native SwiftUI app provides a Finder/Applications-launchable chat window
and a voice popup in the menu bar. Assistant replies are rendered as formatted
Markdown, including paragraphs, lists, tables, and fenced code blocks, rather
than raw Markdown markers. Remote Markdown images are not loaded. Click the
menu bar waveform to open voice mode, then click **Start speaking** and click
**Finish speaking** when done. Voice transcription uses WhisperKit on-device.
The first voice turn downloads the multilingual base model into
`~/Library/Application Support/PersonalAssistant/WhisperKit`; an internet
connection is needed for this one-time download. Afterward, transcription runs
locally and requires microphone permission only—Siri, Dictation, and macOS
Speech Recognition are not used.

### Build and install

For a one-time local build, run these commands from the repository checkout:

```sh
uv sync --group packaging
./macos/build_app.sh
open "dist/Personal Assistant.dmg"
```

In the disk image, drag **Personal Assistant.app** onto **Applications**.
After installation, launch it from Applications, Spotlight, the Dock, or
Launchpad; no terminal or repository checkout is needed for normal use. On
startup it opens the installed Ollama app. Startup does not query Ollama's API,
inspect available models, or download a model. When you send a chat message,
the existing assistant backend uses Ollama's local API to generate the reply;
install/configure Ollama and its model separately before chatting.

The app bundles its Python assistant backend and seeds personal memory into
`~/Library/Application Support/PersonalAssistant` on first run. Ongoing memory
and Obsidian-vault changes remain there across app updates. Backend logs are
stored in `~/Library/Logs/PersonalAssistant`. The helper communicates with the app over
a permission-restricted Unix socket rather than opening a local TCP listener,
so helper IPC does not require local-network permission. Chat turns still use
Ollama's local API to generate responses. The helper stops when the app quits.

The existing terminal workflow remains available and unchanged with
`uv run python main.py`.

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

At the start of a chat session, the assistant sees the app catalog in
[app_registry.json](app_registry.json) and selects one app. That app's tools
remain available on follow-up turns until the user switches apps; tools for
other apps are not exposed at the same time. Requests that clearly require
multiple apps should be handled separately.

Calendar event operations use macOS Calendar.app and create events in the
system-default calendar. Event reminders are native Calendar alerts and
macOS may ask permission for the assistant process to control Calendar.
Daily progress logs remain in `memory/daily_progress.json`; date-range
summaries combine those logs with events read from Calendar.app.

Browser tools use Brave Search for up to five organic web results and the
YouTube Data API for video metadata-based recommendations. Set
`BRAVE_SEARCH_API_KEY` and `YOUTUBE_API_KEY` in the assistant process
environment to enable those searches. Set `GOOGLE_SAFE_BROWSING_API_KEY` to
check URLs against Google's malware and social-engineering lists; without it,
page reads and Safari opens clearly report that reputation was not checked.
Pages are fetched as bounded HTML text only; non-HTML downloads, local/private
network URLs, and URLs reported as unsafe are blocked. Safari opening uses
`osascript` and may require macOS permission to control Safari. YouTube
recommendations use metadata, not transcript or spoken-content verification.

Memory tools are available in every assistant turn. At the start of each typed
or voice session, `memory/personSummary.txt` is included in the system prompt;
other indexed memories can be retrieved on demand. Individual files and
folders can be deleted with explicit confirmation; deletions are confined to
the memory folder and remove matching memory-index entries. The memory root
and its reserved root files cannot be deleted. See
[memory/README.md](memory/README.md) for details.

The assistant interprets relative dates and times using the server's configured
local timezone. Calendar default dates and memory/audit timestamps use that
same timezone; stored timestamps include their UTC offset.

During voice replies, press Command+L to stop narration and continue listening.
macOS may require Accessibility permission for the terminal or app running the
assistant to detect the key.

Memory and app tool calls are audited locally under `logs/`. Each memory/app
log records tool inputs (with string arguments capped at 100 characters) and
tool results; user and assistant chat messages are not logged.
Because tool results and non-text arguments may contain personal data, treat
these local logs as sensitive. Logs rotate at 10 MiB and retain five backups.
See [memory/README.md](memory/README.md) for paths and inspection details.