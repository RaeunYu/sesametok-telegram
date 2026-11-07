"""백엔드 HTTP 클라이언트(`sesametok-backend`).

계약(2026-11 기준, 백엔드 커밋 `07b8e8f`)에서 클라이언트가 지켜야 하는 것:

- 모든 요청에 `Authorization: Bearer <token>`. `/health`만 토큰이 필요 없다.
- `GET /v1/rooms`의 `exposed` 필터는 **`1`/`true`만** 유효하다. 전체를 원하면 **생략**해야
  하고 `exposed=0`을 보내면 `400`이다. 그래서 `exposed_only=False`는 파라미터를 넣지 않는다.
- 발신 결과는 **HTTP 상태만으로 판정하지 않는다.** `unconfirmed`는 200으로도 409로도 온다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .errors import BackendError, BackendUnavailable


@dataclass(frozen=True)
class SendOutcome:
    """발신 200 응답의 구조화된 결과."""

    status: int
    result: str | None
    room: str | None
    chars: int
    client_request_id: str | None
    code: str | None = None
    message: str | None = None


class BackendClient:
    """loopback HTTP API 호출. 토큰은 로그에 남기지 않는다."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        send_timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.send_timeout = send_timeout

    # -- 읽기 ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """토큰이 필요 없는 생존 확인."""

        return self._request("GET", "/health", timeout=self.timeout)

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status", timeout=self.timeout)

    def rooms(self, *, exposed_only: bool = False) -> list[dict[str, Any]]:
        params = {"exposed": "1"} if exposed_only else None
        payload = self._request("GET", "/v1/rooms", params=params, timeout=self.timeout)
        return list(payload.get("rooms") or [])

    def room(self, chat_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/rooms/{_quote(chat_id)}", timeout=self.timeout)

    def messages(
        self,
        chat_id: str,
        *,
        limit: int = 100,
        before: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if before is not None:
            params["before"] = before
        if since is not None:
            params["since"] = since
        return self._request(
            "GET",
            f"/v1/rooms/{_quote(chat_id)}/messages",
            params=params,
            timeout=self.timeout,
        )

    def set_exposed(self, chat_id: str, exposed: bool) -> dict[str, Any]:
        """노출 표시를 바꾼다(멱등). 성공하면 Room 객체를 돌려준다."""

        return self._request(
            "PATCH",
            f"/v1/rooms/{_quote(chat_id)}",
            body={"exposed": bool(exposed)},
            timeout=self.timeout,
        )

    # -- 발신 ---------------------------------------------------------------

    def send_text(
        self,
        chat_id: str,
        text: str,
        client_request_id: str,
        *,
        background_only: bool = True,
        accept_use_policy: bool = True,
    ) -> SendOutcome:
        """텍스트를 보낸다. 포커스 불변식(ADR-0001)에 따라 `background_only`가 기본이다."""

        payload = self._request(
            "POST",
            "/v1/messages",
            body={
                "chat_id": chat_id,
                "text": text,
                "mode": "send",
                "accept_use_policy": bool(accept_use_policy),
                "background_only": bool(background_only),
                "client_request_id": client_request_id,
            },
            timeout=self.send_timeout,
        )
        return _parse_send(payload)

    # -- 내부 ---------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = None if body is None else json.dumps(body).encode("utf-8")

        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if data is not None:
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _decode(response.read())
        except urllib.error.HTTPError as exc:
            raise _error_from(exc) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise BackendUnavailable(f"백엔드에 연결할 수 없습니다: {reason}") from None


def _quote(chat_id: str) -> str:
    return urllib.parse.quote(str(chat_id), safe="")


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BackendError("bad_response", None, f"응답이 JSON이 아닙니다: {exc}") from None
    if not isinstance(payload, dict):
        raise BackendError("bad_response", None, "응답이 JSON 객체가 아닙니다")
    return payload


def _error_from(exc: urllib.error.HTTPError) -> BackendError:
    try:
        payload = _decode(exc.read())
    except BackendError:
        payload = {}
    error = payload.get("error")
    if not isinstance(error, dict):
        error = {}
    code = str(error.get("code") or f"http_{exc.code}")
    message = str(error.get("message") or "")
    retryable = bool(error.get("retryable", False))
    return BackendError(code, exc.code, message, retryable=retryable)


def _parse_send(payload: dict[str, Any]) -> SendOutcome:
    error = payload.get("error")
    if not isinstance(error, dict):
        error = {}
    result = payload.get("result")
    if not isinstance(result, str):
        raise BackendError("bad_response", 200, "발신 응답에 result가 없습니다")
    chars = payload.get("chars")
    return SendOutcome(
        status=200,
        result=result,
        room=payload.get("room"),
        chars=chars if isinstance(chars, int) else 0,
        client_request_id=payload.get("client_request_id"),
        code=error.get("code"),
        message=error.get("message"),
    )
