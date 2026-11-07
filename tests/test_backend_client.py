from __future__ import annotations

import socket

import pytest
from fake_backend import FakeBackend

from sesametok_tg.backend import BackendClient, BackendError, BackendUnavailable


def test_list_all_rooms_omits_exposed_parameter(fake: FakeBackend, client: BackendClient) -> None:
    rooms = client.rooms()
    assert len(rooms) == len(fake.rooms)
    assert "exposed" not in fake.requests[-1].query


def test_exposed_only_sends_flag_and_returns_subset(
    fake: FakeBackend, client: BackendClient
) -> None:
    rooms = client.rooms(exposed_only=True)
    assert fake.requests[-1].query == {"exposed": ["1"]}
    assert {room["chat_id"] for room in rooms} == {"room-a", "room-b"}
    assert all(room["exposed"] for room in rooms)


def test_health_needs_no_token(fake: FakeBackend) -> None:
    client = BackendClient(fake.url, None, timeout=5.0)
    assert client.health()["ok"] is True


def test_wrong_token_raises_unauthorized(fake: FakeBackend) -> None:
    client = BackendClient(fake.url, "wrong", timeout=5.0)
    with pytest.raises(BackendError) as excinfo:
        client.rooms()
    assert (excinfo.value.code, excinfo.value.status) == ("unauthorized", 401)


def test_send_success_returns_outcome(fake: FakeBackend, client: BackendClient) -> None:
    fake.send_reply = (
        200,
        {"result": "sent", "room": "팀방", "chars": 5, "client_request_id": "req-1"},
    )
    outcome = client.send_text("room-b", "안녕하세요", "req-1")

    assert (outcome.result, outcome.room, outcome.chars) == ("sent", "팀방", 5)
    body = fake.requests[-1].body
    assert body is not None
    assert body["mode"] == "send"
    assert body["background_only"] is True  # 포커스 불변식(ADR-0001)
    assert body["accept_use_policy"] is True
    assert body["client_request_id"] == "req-1"
    assert body["text"] == "안녕하세요"


def test_unconfirmed_can_arrive_as_200(fake: FakeBackend, client: BackendClient) -> None:
    fake.send_reply = (
        200,
        {
            "result": "unconfirmed",
            "room": "팀방",
            "chars": 4,
            "client_request_id": "req-2",
            "error": {"code": "not_sent", "message": "확인 불가", "retryable": False},
        },
    )
    outcome = client.send_text("room-b", "테스트", "req-2")
    assert outcome.result == "unconfirmed"
    assert outcome.code == "not_sent"


def test_unconfirmed_can_arrive_as_409(fake: FakeBackend, client: BackendClient) -> None:
    fake.send_reply = (
        409,
        {"error": {"code": "unconfirmed", "message": "결과 미확인", "retryable": False}},
    )
    with pytest.raises(BackendError) as excinfo:
        client.send_text("room-b", "테스트", "req-3")
    assert (excinfo.value.code, excinfo.value.status) == ("unconfirmed", 409)


def test_duplicate_request_is_200_with_null_room(fake: FakeBackend, client: BackendClient) -> None:
    fake.send_reply = (
        200,
        {
            "result": "sent",
            "room": None,
            "chars": 0,
            "client_request_id": "req-4",
            "error": {"code": "duplicate_request", "message": "이미 처리됨", "retryable": False},
        },
    )
    outcome = client.send_text("room-b", "테스트", "req-4")
    assert outcome.code == "duplicate_request"
    assert outcome.room is None
    assert outcome.chars == 0


def test_window_not_open_is_502(fake: FakeBackend, client: BackendClient) -> None:
    fake.send_reply = (
        502,
        {"error": {"code": "window_not_open", "message": "창이 없습니다", "retryable": False}},
    )
    with pytest.raises(BackendError) as excinfo:
        client.send_text("room-b", "테스트", "req-5")
    assert (excinfo.value.code, excinfo.value.status) == ("window_not_open", 502)


def test_chat_id_is_percent_encoded(fake: FakeBackend, client: BackendClient) -> None:
    with pytest.raises(BackendError):
        client.room("a/b c")
    assert fake.requests[-1].path == "/v1/rooms/a%2Fb%20c"


def test_messages_sends_query_parameters(fake: FakeBackend, client: BackendClient) -> None:
    client.messages("room-a", limit=20, before="m-9", since="2026-11-01T00:00:00+00:00")
    query = fake.requests[-1].query
    assert query["limit"] == ["20"]
    assert query["before"] == ["m-9"]
    assert query["since"] == ["2026-11-01T00:00:00+00:00"]


def test_set_exposed_sends_json_boolean(fake: FakeBackend, client: BackendClient) -> None:
    room = client.set_exposed("room-c", True)
    assert room["exposed"] is True
    assert fake.requests[-1].method == "PATCH"
    assert fake.requests[-1].body == {"exposed": True}


def test_unreachable_backend_is_unavailable() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = BackendClient(f"http://127.0.0.1:{port}", "t", timeout=1.0)
    with pytest.raises(BackendUnavailable):
        client.health()
