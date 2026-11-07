from __future__ import annotations

from typing import Any

import pytest
from fake_backend import FakeBackend
from fake_telegram import FakeTelegram

from sesametok_tg.backend import BackendClient
from sesametok_tg.bridge.binding import BindingService
from sesametok_tg.bridge.handler import UpdateHandler
from sesametok_tg.store import Store
from sesametok_tg.telegram.api import TelegramBot

OWNER = 42


@pytest.fixture
def handler(store: Store, client: BackendClient, bot: TelegramBot) -> UpdateHandler:
    return UpdateHandler(
        owner_user_id=OWNER,
        store=store,
        client=client,
        bot=bot,
        bindings=BindingService(store, client),
    )


def _message(
    text: str = "/help",
    *,
    chat_id: int = -100,
    chat_type: str = "group",
    user_id: int = OWNER,
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def _callback(
    data: str = "link:room-a", *, user_id: int = OWNER, chat_id: int = -100
) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": user_id},
            "data": data,
            "message": {"message_id": 77, "chat": {"id": chat_id, "type": "group"}},
        },
    }


def test_ignores_updates_from_other_users(
    handler: UpdateHandler, telegram: FakeTelegram
) -> None:
    handler.handle_update(_message("/help", user_id=999))
    handler.handle_update(_callback(user_id=999))
    assert telegram.calls == []


def test_help_lists_commands(handler: UpdateHandler, telegram: FakeTelegram) -> None:
    handler.handle_update(_message("/help"))
    text = telegram.calls_of("sendMessage")[0]["text"]
    assert "/link" in text
    assert "/history" in text


def test_link_in_private_chat_explains_groups(
    handler: UpdateHandler, telegram: FakeTelegram
) -> None:
    handler.handle_update(_message("/link", chat_id=OWNER, chat_type="private"))
    assert "그룹" in telegram.calls_of("sendMessage")[0]["text"]


def test_link_lists_unbound_exposed_rooms(
    handler: UpdateHandler, telegram: FakeTelegram
) -> None:
    handler.handle_update(_message("/link"))
    payload = telegram.calls_of("sendMessage")[0]
    buttons = [row[0] for row in payload["reply_markup"]["inline_keyboard"]]

    assert [button["text"] for button in buttons] == ["가족방", "팀방"]
    assert [button["callback_data"] for button in buttons] == ["link:room-a", "link:room-b"]


def test_link_ignores_bound_rooms(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    store.bind(-100, "room-a")
    handler.handle_update(_message("/link"))
    buttons = [row[0] for row in telegram.calls_of("sendMessage")[0]["reply_markup"]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["팀방"]


def test_callback_binds_room_and_confirms(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    handler.handle_update(_callback("link:room-a"))

    assert store.room_of_chat(-100) == "room-a"
    assert telegram.calls_of("answerCallbackQuery")[0]["text"] == "연결했습니다"
    assert "연결됨" in telegram.calls_of("editMessageText")[0]["text"]


def test_callback_for_unexposed_room_is_rejected(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    handler.handle_update(_callback("link:room-없음"))
    assert store.room_of_chat(-100) is None
    assert telegram.calls_of("answerCallbackQuery")[0]["text"] == "연결할 수 없는 방입니다"


def test_unlink_removes_binding(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    store.bind(-100, "room-a")
    handler.handle_update(_message("/unlink"))

    assert store.room_of_chat(-100) is None
    assert "해제" in telegram.calls_of("sendMessage")[0]["text"]


def test_history_requires_binding(handler: UpdateHandler, telegram: FakeTelegram) -> None:
    handler.handle_update(_message("/history"))
    assert "먼저 /link" in telegram.calls_of("sendMessage")[0]["text"]


def test_history_reports_empty_when_backend_has_no_messages(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    store.bind(-100, "room-a")
    handler.handle_update(_message("/history 5"))
    assert "메시지가 없습니다" in telegram.calls_of("sendMessage")[0]["text"]


def test_status_reports_connection_state(
    handler: UpdateHandler, telegram: FakeTelegram, store: Store
) -> None:
    handler.handle_update(_message("/link"))  # 방 캐시를 채운다(실제 흐름과 동일)
    store.bind(-100, "room-a")
    handler.handle_update(_message("/status"))
    text = telegram.calls_of("sendMessage")[-1]["text"]

    assert "백엔드:" in text
    assert "이 그룹: «가족방»" in text


def test_status_without_backend(
    handler: UpdateHandler, telegram: FakeTelegram
) -> None:
    handler._client = BackendClient("http://127.0.0.1:1", "t", timeout=0.5)  # noqa: SLF001
    handler.handle_update(_message("/status"))
    assert "연결 실패" in telegram.calls_of("sendMessage")[0]["text"]
