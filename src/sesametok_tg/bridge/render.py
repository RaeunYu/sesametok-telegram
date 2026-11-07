"""카카오톡 메시지 → 텔레그램 텍스트(순수 함수).

표기 정책: 카카오톡 방이 1:1이어도 **항상** `[발신자명]: 본문`을 붙인다. 나중에 그 방에 누가
추가될 수 있고, 표기가 방마다 달라지면 사용자가 발신자를 잘못 읽는다.

본문을 그대로 쓰면 안 되는 경우가 둘 있다.

- `is_feed`: `text`가 사람이 읽는 문장이 아니라 **원본 JSON일 수 있다** → 고정 문구로 대체
- `is_placeholder`: 백엔드가 지원하지 않는 타입을 대체 문구로 바꾼 것 → 원본 타입만 남긴다
"""

from __future__ import annotations

from typing import Any

MAX_MESSAGE_CHARS = 4096

FEED_TEXT = "⚙️ 시스템 메시지"
UNSUPPORTED_TEXT = "⚠️ 미지원 메시지"
EMPTY_TEXT = "(빈 메시지)"
UNKNOWN_SENDER = "알 수 없음"


def sender_label(message: dict[str, Any]) -> str:
    nickname = str(message.get("sender_nickname") or "").strip()
    return nickname or UNKNOWN_SENDER


def render(message: dict[str, Any]) -> str:
    label = sender_label(message)

    if message.get("is_feed"):
        body = FEED_TEXT
    elif message.get("is_placeholder"):
        original = message.get("original_message_type") or message.get("message_type") or "?"
        body = f"{UNSUPPORTED_TEXT} ({original})"
    else:
        body = str(message.get("text") or "").strip() or EMPTY_TEXT

    return f"[{label}]: {body}"


def split_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """텔레그램 4,096자 제한에 맞춰 나눈다. 줄바꿈 경계를 우선한다."""

    if limit <= 0:
        raise ValueError("limit은 1 이상이어야 합니다")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
