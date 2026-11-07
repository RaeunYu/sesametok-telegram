"""텔레그램 Bot API 전송 계층(표준 라이브러리만).

필요한 표면은 다섯 개뿐이다: `getUpdates`, `sendMessage`, `editMessageText`,
`answerCallbackQuery`, `getChatMemberCount`. 프레임워크를 두르지 않아 스텁으로 갈아끼우기 쉽다.

**토큰은 URL 경로에 들어간다.** 그래서 예외 메시지에 예외 원문(`str(exc)`)을 넣지 않는다 —
`HTTPError`의 문자열에는 URL이 포함된다. 로그 쪽에도 `SecretFilter`가 한 겹 더 있다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30.0
MAX_MESSAGE_CHARS = 4096


class TelegramError(Exception):
    """Bot API가 `ok: false`를 돌려줬거나 전송이 실패했다."""

    def __init__(
        self,
        code: int | None,
        description: str,
        *,
        method: str = "",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"{method}: {code} {description}".strip())
        self.code = code
        self.description = description
        self.method = method
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.code in (429, None) or (self.code or 0) >= 500


class TelegramBot:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- 읽기 ---------------------------------------------------------------

    def get_updates(self, offset: int | None = None, *, poll_timeout: int = 25) -> list[dict]:
        payload: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return list(self._call("getUpdates", payload, timeout=poll_timeout + 10))

    def get_chat_member_count(self, chat_id: int) -> int:
        return int(self._call("getChatMemberCount", {"chat_id": chat_id}))

    # -- 쓰기 ---------------------------------------------------------------

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict:
        if len(text) > MAX_MESSAGE_CHARS:
            raise TelegramError(None, "메시지가 4096자를 넘습니다", method="sendMessage")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    # -- 내부 ---------------------------------------------------------------

    def _call(self, method: str, payload: dict | None = None, *, timeout: float | None = None) -> Any:
        url = f"{self.base_url}/bot{self._token}/{method}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = _decode(response.read())
        except urllib.error.HTTPError as exc:
            raise _error_from(exc, method) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # 예외 원문에는 토큰이 든 URL이 들어갈 수 있으므로 타입만 남긴다.
            raise TelegramError(None, type(exc).__name__, method=method) from None

        if not isinstance(body, dict) or not body.get("ok"):
            description = ""
            code = None
            if isinstance(body, dict):
                description = str(body.get("description") or "")
                code = body.get("error_code")
                code = code if isinstance(code, int) else None
            raise TelegramError(code, description, method=method)
        return body.get("result")


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}


def _error_from(exc: urllib.error.HTTPError, method: str) -> TelegramError:
    description = ""
    retry_after: int | None = None
    try:
        payload = _decode(exc.read())
    except OSError:
        payload = {}
    if isinstance(payload, dict):
        description = str(payload.get("description") or "")
        parameters = payload.get("parameters")
        if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int):
            retry_after = parameters["retry_after"]
    return TelegramError(exc.code, description, method=method, retry_after=retry_after)
