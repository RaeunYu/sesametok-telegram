from __future__ import annotations

import sqlite3
from pathlib import Path

from sesametok_tg.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.sqlite3")


def test_bind_and_lookup(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.bind(-100123, "room-a")
        assert store.chat_of_room("room-a") == -100123
        assert store.room_of_chat(-100123) == "room-a"
        assert store.unbind(-100123) is True
        assert store.room_of_chat(-100123) is None
        assert store.unbind(-100123) is False


def test_binding_replaces_conflicting_chat_or_room(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.bind(1, "room-a")
        store.bind(2, "room-a")  # 같은 방을 다른 그룹으로 옮김
        assert store.chat_of_room("room-a") == 2
        assert len(store.bindings()) == 1

        store.bind(2, "room-b")  # 같은 그룹을 다른 방으로 옮김
        assert store.room_of_chat(2) == "room-b"
        assert len(store.bindings()) == 1


def test_delivery_roundtrip(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.record_delivery(
            telegram_chat_id=-100,
            telegram_message_id=77,
            kakao_chat_id="room-a",
            kakao_message_id="m-1",
            seq=10,
        )
        by_kakao = store.delivery_for_kakao("room-a", "m-1")
        assert by_kakao is not None
        assert by_kakao["telegram_message_id"] == 77

        by_telegram = store.delivery_for_telegram(-100, 77)
        assert by_telegram is not None
        assert by_telegram["kakao_message_id"] == "m-1"


def test_delivery_table_has_no_body_column(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        columns = {
            row[1]
            for row in store._conn.execute("PRAGMA table_info(deliveries)")  # noqa: SLF001
        }
    assert "text" not in columns
    assert "message_text" not in columns


def test_meta_roundtrip_and_int(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        assert store.get_int_meta("last_seq") is None
        store.set_meta("last_seq", 6041)
        assert store.get_int_meta("last_seq") == 6041
        store.set_meta("last_seq", "6042")
        assert store.get_int_meta("last_seq") == 6042
        assert store.get_int_meta("missing", 7) == 7


def test_rooms_upsert_and_exposed_filter(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.upsert_rooms(
            [
                {"chat_id": "a", "chat_name": "가", "chat_type": "group", "exposed": True,
                 "last_message_at": "2026-11-01T00:00:00+00:00", "message_count": 3},
                {"chat_id": "b", "chat_name": "나", "chat_type": "direct", "exposed": False,
                 "last_message_at": None, "message_count": 0},
            ]
        )
        assert [room["chat_id"] for room in store.rooms()] == ["a", "b"]
        assert [room["chat_id"] for room in store.rooms(exposed_only=True)] == ["a"]

        store.upsert_rooms(
            [{"chat_id": "a", "chat_name": "가(수정)", "chat_type": "group",
              "exposed": False, "last_message_at": None, "message_count": 4}]
        )
        room = store.room("a")
        assert room is not None
        assert room["chat_name"] == "가(수정)"
        assert room["exposed"] is False


def test_room_cursor(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        assert store.room_cursor("a") is None
        store.set_room_cursor("a", "2026-11-01T00:00:00+00:00")
        assert store.room_cursor("a") == "2026-11-01T00:00:00+00:00"


def test_reopen_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path) as store:
        store.bind(1, "room-a")
        store.set_meta("last_seq", 5)
    with Store(path) as store:
        assert store.room_of_chat(1) == "room-a"
        assert store.get_int_meta("last_seq") == 5


def test_schema_version_is_set(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path):
        pass
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
