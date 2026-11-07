from __future__ import annotations

import pytest
from fake_telegram import FakeTelegram

from sesametok_tg.backend import Event
from sesametok_tg.bridge import InboundPipeline
from sesametok_tg.store import Store


def _payload(**overrides: object) -> dict:
    base: dict = {
        "message_id": "m-1",
        "chat_id": "room-a",
        "chat_name": "가족방",
        "chat_type": "group",
        "sender_id": "u1",
        "sender_nickname": "홍길동",
        "timestamp": "2026-11-07T00:00:00+00:00",
        "text": "안녕하세요",
        "message_type": "text",
        "is_feed": False,
        "is_placeholder": False,
        "original_message_type": None,
        "is_from_me": False,
        "origin_client_request_id": None,
    }
    base.update(overrides)
    return base


def _event(kind: str = "message.created", seq: int = 10, **overrides: object) -> Event:
    return Event(seq=seq, kind=kind, payload=_payload(**overrides))


@pytest.fixture
def pipeline(store: Store, bot) -> InboundPipeline:  # type: ignore[no-untyped-def]
    return InboundPipeline(store, bot)


def test_delivers_to_bound_group(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    store.bind(-100, "room-a")
    result = pipeline.handle(_event())

    assert result.action == "sent"
    sent = telegram.calls_of("sendMessage")[0]
    assert sent["chat_id"] == -100
    assert sent["text"] == "[홍길동]: 안녕하세요"


def test_skips_unbound_room(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    result = pipeline.handle(_event())
    assert result.reason == "unbound"
    assert telegram.calls_of("sendMessage") == []


def test_skips_echo(store: Store, telegram: FakeTelegram, pipeline: InboundPipeline) -> None:
    store.bind(-100, "room-a")
    result = pipeline.handle(_event(origin_client_request_id="req-1"))
    assert result.reason == "echo"
    assert telegram.calls_of("sendMessage") == []


def test_skips_duplicate(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    store.bind(-100, "room-a")
    pipeline.handle(_event())
    result = pipeline.handle(_event(seq=11))

    assert result.reason == "duplicate"
    assert len(telegram.calls_of("sendMessage")) == 1


def test_edits_on_update(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    store.bind(-100, "room-a")
    pipeline.handle(_event())
    result = pipeline.handle(_event(kind="message.updated", seq=11, text="수정된 본문"))

    assert result.action == "edited"
    edit = telegram.calls_of("editMessageText")[0]
    assert edit["text"] == "[홍길동]: 수정된 본문"


def test_update_without_delivery_is_sent_as_new(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    store.bind(-100, "room-a")
    result = pipeline.handle(_event(kind="message.updated", text="나중에 고친 메시지"))

    assert result.action == "sent"
    assert len(telegram.calls_of("sendMessage")) == 1


def test_splits_long_message(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    store.bind(-100, "room-a")
    pipeline.handle(_event(text="가" * 5000))

    sends = telegram.calls_of("sendMessage")
    assert len(sends) == 2
    assert all(len(sent["text"]) <= 4096 for sent in sends)


def test_records_room_cursor(store: Store, pipeline: InboundPipeline) -> None:
    store.bind(-100, "room-a")
    pipeline.handle(_event())
    assert store.room_cursor("room-a") == "2026-11-07T00:00:00+00:00"


def test_ignores_non_message_kinds(
    telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    event = Event(seq=5, kind="send.result", payload={"client_request_id": "r"})
    assert pipeline.handle(event).reason == "unsupported_kind"
    assert telegram.calls_of("sendMessage") == []


def test_send_failure_is_reported_not_raised(
    store: Store, telegram: FakeTelegram, pipeline: InboundPipeline
) -> None:
    from fake_telegram import ErrorSpec

    store.bind(-100, "room-a")
    telegram.failures["sendMessage"] = ErrorSpec(403, "Forbidden: bot was kicked")

    result = pipeline.handle(_event())
    assert result.reason == "send_failed:403"
    assert store.delivery_for_kakao("room-a", "m-1") is None
