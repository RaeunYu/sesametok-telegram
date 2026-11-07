"""이벤트 → 텔레그램 그룹 전달.

판단 순서가 곧 안전장치다.

1. `message.created`/`message.updated`만 본다.
2. **에코 억제**: `origin_client_request_id`가 있으면 내가 보낸 것이므로 전달하지 않는다.
3. **연결 확인**: 그 방이 어떤 그룹에도 연결되지 않았으면 아무 데도 보내지 않는다.
4. **중복 방지**: 이미 전달한 `message_id`는 다시 보내지 않는다.
5. `message.updated`는 원본 텔레그램 메시지를 **수정**한다. 원본을 모르면 새로 보낸다(폴백).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..backend import Event
from ..store import Store
from ..telegram.api import TelegramBot, TelegramError
from .render import render, split_text

logger = logging.getLogger(__name__)

MESSAGE_KINDS = ("message.created", "message.updated")


@dataclass(frozen=True)
class DeliveryResult:
    action: str  # "sent" | "edited" | "skipped"
    reason: str | None = None


class InboundPipeline:
    def __init__(self, store: Store, bot: TelegramBot) -> None:
        self._store = store
        self._bot = bot

    def handle(self, event: Event) -> DeliveryResult:
        if event.kind not in MESSAGE_KINDS:
            return DeliveryResult("skipped", "unsupported_kind")

        message = event.payload
        kakao_message_id = message.get("message_id")
        kakao_chat_id = message.get("chat_id")
        if not isinstance(kakao_message_id, str) or not isinstance(kakao_chat_id, str):
            return DeliveryResult("skipped", "bad_payload")

        if message.get("origin_client_request_id"):
            return DeliveryResult("skipped", "echo")

        telegram_chat_id = self._store.chat_of_room(kakao_chat_id)
        if telegram_chat_id is None:
            return DeliveryResult("skipped", "unbound")

        existing = self._store.delivery_for_kakao(kakao_chat_id, kakao_message_id)
        text = render(message)

        if event.kind == "message.updated" and existing is not None:
            try:
                self._bot.edit_message_text(
                    telegram_chat_id, existing["telegram_message_id"], text
                )
                return DeliveryResult("edited")
            except TelegramError as exc:
                # 수정할 수 없는 경우(너무 오래된 메시지 등)는 새로 보내는 편이 낫다.
                logger.info("메시지 수정 실패(code=%s) — 새로 보냅니다", exc.code)
                return self._send(event, telegram_chat_id, kakao_chat_id, kakao_message_id, text)

        if existing is not None:
            return DeliveryResult("skipped", "duplicate")

        return self._send(event, telegram_chat_id, kakao_chat_id, kakao_message_id, text)

    # -- 내부 ---------------------------------------------------------------

    def _send(
        self,
        event: Event,
        telegram_chat_id: int,
        kakao_chat_id: str,
        kakao_message_id: str,
        text: str,
    ) -> DeliveryResult:
        parts = split_text(text)
        first: dict[str, Any] = {}
        try:
            first = self._bot.send_message(telegram_chat_id, parts[0])
            for part in parts[1:]:
                self._bot.send_message(telegram_chat_id, part)
        except TelegramError as exc:
            logger.warning(
                "그룹 전달 실패(chat_id=%s, code=%s)", telegram_chat_id, exc.code
            )
            return DeliveryResult("skipped", f"send_failed:{exc.code}")

        message_id = first.get("message_id")
        if isinstance(message_id, int):
            self._store.record_delivery(
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=message_id,
                kakao_chat_id=kakao_chat_id,
                kakao_message_id=kakao_message_id,
                seq=event.seq,
            )
        timestamp = event.payload.get("timestamp")
        if isinstance(timestamp, str):
            self._store.set_room_cursor(kakao_chat_id, timestamp)
        return DeliveryResult("sent")
