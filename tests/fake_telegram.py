"""가짜 텔레그램 Bot API 서버(표준 라이브러리만).

토큰이 URL 경로(`/bot<token>/<method>`)에 들어가는 실제 구조를 그대로 흉내 낸다. 그래야
"토큰이 어디로 가는지"와 "호출 파라미터"를 테스트가 직접 검증할 수 있다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ErrorSpec:
    code: int
    description: str
    retry_after: int | None = None


@dataclass
class FakeTelegram:
    token: str = "123456789:TESTTOKEN"
    member_count: int = 2
    updates: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    failures: dict[str, ErrorSpec] = field(default_factory=dict)
    next_message_id: int = 500
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def __enter__(self) -> FakeTelegram:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.app = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, args=(0.01,), daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self._server is not None, "FakeTelegram을 with 문으로 열어야 한다"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]

    def route(self, method: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((method, payload))

        failure = self.failures.get(method)
        if failure is not None:
            body: dict[str, Any] = {
                "ok": False,
                "error_code": failure.code,
                "description": failure.description,
            }
            if failure.retry_after is not None:
                body["parameters"] = {"retry_after": failure.retry_after}
            return failure.code, body

        if method == "getUpdates":
            offset = payload.get("offset")
            pending = self.updates
            if isinstance(offset, int):
                pending = [update for update in pending if update.get("update_id", 0) >= offset]
            self.updates = [update for update in self.updates if update not in pending]
            return 200, {"ok": True, "result": pending}

        if method == "sendMessage":
            self.next_message_id += 1
            return 200, {
                "ok": True,
                "result": {
                    "message_id": self.next_message_id,
                    "chat": {"id": payload.get("chat_id")},
                    "text": payload.get("text"),
                },
            }

        if method == "editMessageText":
            return 200, {
                "ok": True,
                "result": {"message_id": payload.get("message_id"), "text": payload.get("text")},
            }

        if method == "answerCallbackQuery":
            return 200, {"ok": True, "result": True}

        if method == "getChatMemberCount":
            return 200, {"ok": True, "result": self.member_count}

        return 404, {"ok": False, "error_code": 404, "description": "Not Found"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeTelegram/1.0"

    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        app: FakeTelegram = self.server.app  # type: ignore[attr-defined]
        path = urlsplit(self.path).path
        prefix = f"/bot{app.token}/"
        if not path.startswith(prefix):
            self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
            return
        method = path[len(prefix) :]
        payload = self._read_json()
        status, body = app.route(method, payload)
        self._send(status, body)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return {}
        raw = self.rfile.read(int(raw_length))
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
