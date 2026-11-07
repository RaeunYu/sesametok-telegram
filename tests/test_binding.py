from __future__ import annotations

from sesametok_tg.backend import BackendClient
from sesametok_tg.bridge.binding import BindingService
from sesametok_tg.store import Store


def _service(store: Store, client: BackendClient) -> BindingService:
    service = BindingService(store, client)
    service.refresh()
    return service


def test_refresh_caches_exposed_rooms_only(store: Store, client: BackendClient) -> None:
    service = BindingService(store, client)

    assert service.refresh() == 2  # 노출된 방만 내려온다
    assert [room["chat_id"] for room in store.rooms(exposed_only=True)] == ["room-a", "room-b"]
    assert store.room("room-c") is None


def test_options_exclude_bound_rooms(store: Store, client: BackendClient) -> None:
    service = _service(store, client)
    assert [option.chat_id for option in service.options()] == ["room-a", "room-b"]

    store.bind(-100, "room-a")
    assert [option.chat_id for option in service.options()] == ["room-b"]


def test_bind_requires_exposed_room(store: Store, client: BackendClient) -> None:
    service = _service(store, client)

    assert service.bind(-100, "room-c") is None
    assert store.room_of_chat(-100) is None


def test_bind_returns_room_name(store: Store, client: BackendClient) -> None:
    service = _service(store, client)
    assert service.bind(-100, "room-a") == "가족방"
    assert store.room_of_chat(-100) == "room-a"
    assert service.room_name(-100) == "가족방"


def test_bind_moves_room_to_another_group(store: Store, client: BackendClient) -> None:
    service = _service(store, client)
    service.bind(-100, "room-a")

    assert service.bind(-200, "room-a") == "가족방"
    assert store.room_of_chat(-100) is None
    assert store.chat_of_room("room-a") == -200


def test_unbind(store: Store, client: BackendClient) -> None:
    service = _service(store, client)
    service.bind(-100, "room-a")

    assert service.unbind(-100) == "가족방"
    assert service.unbind(-100) is None
    assert service.room_name(-100) is None
