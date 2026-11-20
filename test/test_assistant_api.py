import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from http.client import HTTPConnection
from unittest.mock import patch

import assistant_api


class AssistantAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.token = "test-token-with-more-than-thirty-two-characters"
        self.server = assistant_api._AssistantHTTPServer(("127.0.0.1", 0), self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        token: str | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        connection = HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        encoded = json.dumps(body).encode() if body is not None else None
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_requires_bearer_token_for_health(self) -> None:
        status, _ = self._request("GET", "/health")
        self.assertEqual(status, 401)

        status, payload = self._request(
            "GET", "/health", token=self.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_rejects_cross_origin_requests(self) -> None:
        status, _ = self._request(
            "POST", "/sessions", {}, token=self.token, origin="https://example.com"
        )
        self.assertEqual(status, 403)

    def test_creates_session_and_preserves_chat_context(self) -> None:
        with patch.object(assistant_api.assistant, "build_system_prompt", return_value="system"):
            status, payload = self._request(
                "POST", "/sessions", {}, token=self.token
            )
        self.assertEqual(status, 201)
        session_id = payload["session_id"]

        with patch.object(
            assistant_api.assistant,
            "run_assistant",
            side_effect=lambda history, tool_context: (
                history.append({"role": "assistant", "content": "Hello."}) or "Hello."
            ),
        ) as run_assistant:
            status, first_response = self._request(
                "POST",
                f"/sessions/{session_id}/messages",
                {"text": "Hi"},
                token=self.token,
            )
            status, second_response = self._request(
                "POST",
                f"/sessions/{session_id}/messages",
                {"text": "Another turn"},
                token=self.token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(first_response, {"response": "Hello."})
        self.assertEqual(second_response, {"response": "Hello."})
        history = run_assistant.call_args.args[0]
        context = run_assistant.call_args.kwargs["tool_context"]
        self.assertEqual(history[0]["content"], "system")
        self.assertEqual(
            [message["content"] for message in history[1:]],
            ["Hi", "Hello.", "Another turn", "Hello."],
        )
        self.assertIs(context, self.server.sessions[session_id].tool_context)

    def test_rolls_back_partial_history_when_assistant_fails(self) -> None:
        with patch.object(assistant_api.assistant, "build_system_prompt", return_value="system"):
            _, payload = self._request("POST", "/sessions", {}, token=self.token)
        session_id = payload["session_id"]

        def fail_after_mutating(history: list[dict[str, object]], **_: object) -> str:
            history.append({"role": "assistant", "content": "partial"})
            raise RuntimeError("model unavailable")

        with patch.object(
            assistant_api.assistant, "run_assistant", side_effect=fail_after_mutating
        ):
            status, response = self._request(
                "POST",
                f"/sessions/{session_id}/messages",
                {"text": "Hi"},
                token=self.token,
            )

        self.assertEqual(status, 502)
        self.assertIn("model unavailable", response["error"])
        self.assertEqual(
            self.server.sessions[session_id].history,
            [{"role": "system", "content": "system"}],
        )

    def test_rejects_invalid_message_and_unknown_session(self) -> None:
        status, _ = self._request(
            "POST", "/sessions/missing/messages", {"text": "Hi"}, token=self.token
        )
        self.assertEqual(status, 404)

        status, _ = self._request(
            "POST", "/sessions/missing/messages", {"text": " "}, token=self.token
        )
        self.assertEqual(status, 400)

    def test_unix_socket_bridge_requires_no_bearer_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            socket_path = Path(temporary_directory) / "assistant.sock"
            server = assistant_api._AssistantUnixServer(str(socket_path), "")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.connect(str(socket_path))
                connection.sendall(
                    b"GET /health HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                response = bytearray()
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        headers, body = bytes(response).split(b"\r\n\r\n", 1)
        self.assertIn(b"200 OK", headers)
        self.assertEqual(json.loads(body), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
