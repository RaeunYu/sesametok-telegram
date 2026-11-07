from __future__ import annotations

from sesametok_tg.bridge.render import MAX_MESSAGE_CHARS, render, split_text


def _message(**overrides: object) -> dict:
    base: dict = {
        "message_id": "m-1",
        "chat_id": "r",
        "sender_nickname": "홍길동",
        "text": "안녕하세요",
        "is_feed": False,
        "is_placeholder": False,
        "original_message_type": None,
        "chat_type": "group",
    }
    base.update(overrides)
    return base


def test_prefix_is_always_present() -> None:
    assert render(_message()) == "[홍길동]: 안녕하세요"
    # 1:1 방에서도 표기는 동일하다(확정 정책).
    assert render(_message(chat_type="direct")) == "[홍길동]: 안녕하세요"


def test_feed_hides_raw_json() -> None:
    text = render(_message(is_feed=True, text='{"feedType": 1, "members": ["u1"]}'))
    assert "feedType" not in text
    assert "시스템 메시지" in text


def test_placeholder_shows_original_type() -> None:
    text = render(
        _message(is_placeholder=True, original_message_type="type_7", text="[지원하지 않는 메시지]")
    )
    assert "미지원" in text
    assert "type_7" in text


def test_empty_body_becomes_placeholder() -> None:
    assert render(_message(text="   ")).endswith("(빈 메시지)")


def test_missing_nickname_falls_back() -> None:
    assert render(_message(sender_nickname="")) == "[알 수 없음]: 안녕하세요"


def test_split_short_text_returns_single_part() -> None:
    assert split_text("짧음") == ["짧음"]


def test_split_prefers_newline_boundary() -> None:
    text = "a" * 10 + "\n" + "b" * 10
    parts = split_text(text, limit=12)
    assert parts == ["a" * 10, "b" * 10]


def test_split_hard_cut_without_newline() -> None:
    parts = split_text("c" * 25, limit=10)
    assert [len(part) for part in parts] == [10, 10, 5]


def test_default_limit_is_telegram_cap() -> None:
    assert MAX_MESSAGE_CHARS == 4096
