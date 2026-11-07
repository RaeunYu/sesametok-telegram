"""계약을 고정하는 가짜 백엔드(표준 라이브러리만).

실제 백엔드는 CLI로는 카카오톡 DB 없이 뜨지 않으므로, 클라이언트 계약 테스트는 이 스텁에
대해 돈다. 여기서 고정하는 동작은 **백엔드 인수인계 문서에서 확인한 것**과 같다.

- `/health`를 뺀 모든 경로는 Bearer 토큰이 필요하다.
- `GET /v1/rooms?exposed=`는 `1`/`true`만 유효하고, 그 밖(`0`, `2`)은 `400`이다.
- 발신은 `200`(성공/미확인/중복)과 `409`(미확인) 두 형태로 온다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: dict[str, Any] | None
    headers: dict[str, str]


@dataclass
class FakeBackend:
    token: str = "test-token"
    rooms: list[dict[str, Any]] = field(default_factory=list)
    status_payload: dict[str, Any] = field(default_factory=dict)
    send_reply: tuple[int, dict[str, Any]] = (200, {})
    requests: list[RecordedRequest] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        if not self.rooms:
            self.rooms = [
                _room("room-a", "가족방", "group", exposed=True),
                _room("room-b", "팀방", "group", exposed=True),
                _room("room-c", "친구", "direct", exposed=False),
            ]
        if not self.status_payload:
            self.status_payload = {
                "uptime_seconds": 10,
                "archive": {"rooms": 3, "messages": 12, "event_seq": 42, "oldest_event_seq": 0},
                "webhooks": {"total": 0, "active": 0},
                "version": "0.1.0",
            }

    # -- 수명주기 -----------------------------------------------------------

    def __enter__(self) -> FakeBackend:
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
        assert self._server is not None, "FakeBackend를 with 문으로 열어야 한다"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- 라우팅 -------------------------------------------------------------

    def authorized(self, header: str | None) -> bool:
        return header == f"Bearer {self.token}"

    def find_room(self, chat_id: str) -> dict[str, Any] | None:
        for room in self.rooms:
            if room["chat_id"] == chat_id:
                return room
        return None

    def route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if path == "/health" and method == "GET":
            return 200, {
                "ok": True,
                "kakaotalk_running": True,
                "permissions_ok": True,
                "polling": True,
                "last_sync_at": None,
                "consecutive_failures": 0,
            }
        if path == "/v1/status" and method == "GET":
            return 200, self.status_payload

        if path == "/v1/rooms" and method == "GET":
            values = query.get("exposed")
            if values is None:
                return 200, {"rooms": self.rooms}
            if values[0] in ("1", "true"):
                return 200, {"rooms": [room for room in self.rooms if room["exposed"]]}
            return _error(400, "bad_request", "exposed는 1 또는 true만 허용합니다")

        if path.startswith("/v1/rooms/"):
            rest = path[len("/v1/rooms/") :]
            if rest.endswith("/messages") and method == "GET":
                chat_id = unquote(rest[: -len("/messages")].rstrip("/"))
                return 200, {
                    "chat_id": chat_id,
                    "messages": [],
                    "next_before": None,
                    "has_more": False,
                }
            chat_id = unquote(rest)
            room = self.find_room(chat_id)
            if room is None:
                return _error(404, "room_not_found", "없는 방")
            if method == "GET":
                return 200, room
            if method == "PATCH":
                exposed = (body or {}).get("exposed")
                if not isinstance(exposed, bool):
                    return _error(400, "bad_request", "exposed는 JSON boolean이어야 합니다")
                room["exposed"] = exposed
                return 200, room
            return _error(405, "method_not_allowed", "허용되지 않은 메서드")

        if path == "/v1/messages" and method == "POST":
            return self.send_reply

        return _error(404, "route_not_found", "없는 경로")


def _room(chat_id: str, name: str, chat_type: str, *, exposed: bool) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "chat_name": name,
        "chat_type": chat_type,
        "last_message_at": "2026-11-01T00:00:00+00:00",
        "message_count": 3,
        "exposed": exposed,
    }


def _error(status: int, code: str, message: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message, "retryable": False}}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeBackend/1.0"

    def log_message(self, *args: object) -> None:  # 테스트 출력을 조용히
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        app: FakeBackend = self.server.app  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        body = self._read_json()
        app.requests.append(
            RecordedRequest(
                method=self.command,
                path=parts.path,
                query=query,
                body=body,
                headers={key: value for key, value in self.headers.items()},
            )
        )

        if parts.path != "/health" and not app.authorized(self.headers.get("Authorization")):
            self._send(*_error(401, "unauthorized", "토큰이 필요합니다"))
            return

        try:
            status, payload = app.route(self.command, parts.path, query, body)
        except Exception as exc:  # noqa: BLE001 - 스텁이 조용히 실패하지 않게
            self._send(*_error(500, "internal_error", str(exc)))
            return
        self._send(status, payload)

    def _read_json(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return None
        raw = self.rfile.read(int(raw_length))
        if not raw:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
