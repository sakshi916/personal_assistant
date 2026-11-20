"""Loopback-only HTTP bridge for the native macOS assistant client."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import socketserver
import sys
import threading
from typing import Any
from urllib.parse import urlsplit
import uuid

import app_tools
import assistant
import audit_log
import memory_tools

from apps import calendar_tools, text_editor_tools

MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_PORT = 8765


def _configure_user_data() -> None:
    data_directory = os.environ.get("PERSONAL_ASSISTANT_DATA_DIR")
    if not data_directory:
        return

    data_root = os.path.abspath(data_directory)
    memory_root = os.path.join(data_root, "memory")
    if not os.path.exists(memory_root):
        bundled_memory = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "memory"
        )
        if os.path.isdir(bundled_memory):
            shutil.copytree(bundled_memory, memory_root)
        else:
            os.makedirs(memory_root)

    memory_tools.MEMORY_ROOT = Path(memory_root)
    calendar_tools.MEMORY_ROOT = Path(memory_root)
    calendar_tools.PROGRESS_PATH = calendar_tools.MEMORY_ROOT / "daily_progress.json"
    text_editor_tools.VAULT_ROOT = (
        calendar_tools.MEMORY_ROOT / "obsidian" / "personal"
    )
    audit_log.LOG_ROOT = Path(
        os.environ.get(
            "PERSONAL_ASSISTANT_LOG_DIR",
            Path.home() / "Library" / "Logs" / "PersonalAssistant",
        )
    )


class _Session:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": assistant.build_system_prompt()}
        ]
        self.tool_context = app_tools.ToolContext()
        self.lock = threading.Lock()


class _AssistantServerState:
    daemon_threads = True
    allow_reuse_address = True

    def _initialize_state(self, token: str) -> None:
        self.token = token
        self.sessions: dict[str, _Session] = {}
        self.sessions_lock = threading.Lock()


class _AssistantHTTPServer(_AssistantServerState, ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], token: str) -> None:
        self._initialize_state(token)
        super().__init__(address, _AssistantRequestHandler)


class _AssistantUnixServer(_AssistantServerState, socketserver.ThreadingUnixStreamServer):
    def __init__(self, address: str, token: str) -> None:
        self._initialize_state(token)
        super().__init__(address, _AssistantRequestHandler)


class _AssistantRequestHandler(BaseHTTPRequestHandler):
    server: _AssistantHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path != "/health":
            self._send_json(404, {"error": "Not found"})
            return
        self._send_json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if not self._valid_local_request():
            self._send_json(403, {"error": "Requests must come from this Mac"})
            return

        try:
            body = self._read_json_object()
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"error": str(error)})
            return

        if self.path == "/sessions":
            self._create_session()
            return
        if self.path == "/shutdown":
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            self._send_json(200, {"status": "shutting_down"})
            return
        if self.path == "/shutdown":
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            self._send_json(200, {"status": "shutting_down"})
            return

        prefix = "/sessions/"
        suffix = "/messages"
        if self.path.startswith(prefix) and self.path.endswith(suffix):
            session_id = self.path[len(prefix) : -len(suffix)]
            self._send_message(session_id, body)
            return

        self._send_json(404, {"error": "Not found"})

    def _authorized(self) -> bool:
        if not self._valid_local_request():
            self._send_json(403, {"error": "Requests must come from this Mac"})
            return False
        if self.headers.get("Origin") or self.headers.get("Access-Control-Request-Method"):
            self._send_json(403, {"error": "Cross-origin requests are not allowed"})
            return False
        if isinstance(self.server, _AssistantUnixServer):
            return True
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(supplied, expected):
            self._send_json(401, {"error": "Unauthorized"})
            return False
        return True

    def _valid_local_request(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            hostname = urlsplit(f"//{host}").hostname
        except ValueError:
            return False
        return (
            (
                isinstance(self.server, _AssistantUnixServer)
                or self.client_address[0] == "127.0.0.1"
            )
            and hostname in {"127.0.0.1", "localhost"}
        )

    def _read_json_object(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content-Length must be an integer") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body exceeds the allowed size")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _create_session(self) -> None:
        try:
            session = _Session()
        except Exception as error:
            self._send_json(500, {"error": f"Could not initialize assistant: {error}"})
            return
        session_id = str(uuid.uuid4())
        with self.server.sessions_lock:
            self.server.sessions[session_id] = session
        self._send_json(201, {"session_id": session_id})

    def _send_message(self, session_id: str, body: dict[str, Any]) -> None:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            self._send_json(400, {"error": "'text' must be a non-empty string"})
            return
        if len(text) > 16_000:
            self._send_json(400, {"error": "'text' exceeds 16000 characters"})
            return
        with self.server.sessions_lock:
            session = self.server.sessions.get(session_id)
        if session is None:
            self._send_json(404, {"error": "Session not found"})
            return

        with session.lock:
            history_length = len(session.history)
            session.history.append({"role": "user", "content": text})
            try:
                answer = assistant.run_assistant(
                    session.history, tool_context=session.tool_context
                )
            except Exception as error:
                del session.history[history_length:]
                self._send_json(502, {"error": f"Assistant request failed: {error}"})
                return
        self._send_json(200, {"response": answer})

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if status >= 400:
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        if isinstance(self.server, _AssistantUnixServer):
            print(f"assistant-api: {format % args}", file=sys.stderr)
            return
        super().log_message(format, *args)


def main() -> None:
    try:
        port = int(os.environ.get("PERSONAL_ASSISTANT_API_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise SystemExit("PERSONAL_ASSISTANT_API_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise SystemExit("PERSONAL_ASSISTANT_API_PORT must be between 1 and 65535")

    _configure_user_data()
    socket_path = os.environ.get("PERSONAL_ASSISTANT_API_SOCKET")
    if socket_path:
        socket_file = Path(socket_path)
        socket_file.parent.mkdir(parents=True, exist_ok=True)
        socket_file.unlink(missing_ok=True)
        server = _AssistantUnixServer(str(socket_file), "")
        socket_file.chmod(0o600)
        print(f"Assistant API listening on Unix socket {socket_file}", flush=True)
    else:
        token = os.environ.get("PERSONAL_ASSISTANT_API_TOKEN", "")
        if len(token) < 32:
            raise SystemExit(
                "PERSONAL_ASSISTANT_API_TOKEN must contain at least 32 characters"
            )
        server = _AssistantHTTPServer(("127.0.0.1", port), token)
        socket_file = None
        print(f"Assistant API listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if socket_file is not None:
            socket_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
