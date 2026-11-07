"""SSE 구독과 프레임 해석. 네트워크 없이 재연결·갭·409를 검증한다."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from sesametok_tg.backend.errors import BackendError
from sesametok_tg.backend.stream import (
    Event,
    EventStream,
    Gap,
    ReplayUnavailable,
    iter_frames,
    parse_frame,
)


class StopTest(BaseException):
    """테스트에서 무한 재연결 루프를 끊기 위한 신호(OSError가 아니어야 한다)."""


def _frame(seq: int, kind: str = "message.created", payload: dict | None = None) -> str:
    body = {
        "seq": seq,
        "kind": kind,
        "payload": payload or {"message_id": f"m{seq}"},
        "created_at": "2026-11-07T00:00:00+00:00",
    }
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


class Scripted:
    def __init__(self, *chunks: str | BaseException) -> None:
        self.chunks = list(chunks)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        if not self.chunks:
            raise StopTest
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return io.BytesIO(item.encode("utf-8"))


def _http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:8756/v1/events",
        code,
        "error",
        {},  # type: ignore[arg-type]
        io.BytesIO(json.dumps(payload).encode()),
    )


# -- 프레임 해석 ------------------------------------------------------------


def test_parse_frame_reads_standard_fields() -> None:
    event = parse_frame(_frame(12).strip().split("\n"))
    assert event == Event(
        seq=12,
        kind="message.created",
        payload={"message_id": "m12"},
        created_at="2026-11-07T00:00:00+00:00",
    )


def test_parse_frame_joins_multiline_data() -> None:
    lines = [
        "id: 3",
        'data: {"seq": 3,',
        'data:  "kind": "room.updated", "payload": {"chat_id": "r"}}',
    ]
    event = parse_frame(lines)
    assert event is not None
    assert (event.seq, event.kind) == (3, "room.updated")


def test_parse_frame_returns_none_for_garbage() -> None:
    assert parse_frame(["data: not-json"]) is None
    assert parse_frame(["id: 1"]) is None


def test_iter_frames_drops_comments_and_keeps_frames() -> None:
    lines = iter([": ping", "", *_frame(1).splitlines(), "", ": ping", "", *_frame(2).splitlines()])
    frames = list(iter_frames(lines))
    assert [parse_frame(frame).seq for frame in frames] == [1, 2]  # type: ignore[union-attr]


# -- 스트림 동작 ------------------------------------------------------------


def test_keepalive_does_not_break_stream() -> None:
    script = Scripted(": ping\n\n" + _frame(2))
    stream = EventStream("http://x", "t", since=1, opener=script, sleeper=lambda _: None)
    assert next(stream.iter_events()).seq == 2  # type: ignore[union-attr]


def test_reconnects_from_last_processed_seq() -> None:
    script = Scripted(_frame(5) + _frame(6), _frame(7), StopTest())
    stream = EventStream("http://x", "t", since=4, opener=script, sleeper=lambda _: None)

    seen: list[int] = []
    with pytest.raises(StopTest):
        for item in stream.iter_events():
            assert isinstance(item, Event)
            seen.append(item.seq)

    assert seen == [5, 6, 7]
    urls = [request.full_url for request in script.requests]
    assert urls[0].endswith("?since=4")
    assert urls[1].endswith("?since=6")


def test_seq_gap_breaks_connection_and_resumes_from_last_seq() -> None:
    script = Scripted(_frame(5) + _frame(7), _frame(6) + _frame(7), StopTest())
    stream = EventStream("http://x", "t", since=4, opener=script, sleeper=lambda _: None)

    items: list[Event | Gap] = []
    with pytest.raises(StopTest):
        for item in stream.iter_events():
            items.append(item)

    assert items[0] == Event(5, "message.created", {"message_id": "m5"}, "2026-11-07T00:00:00+00:00")
    assert items[1] == Gap(expected_seq=6, received_seq=7)
    # 갭을 만난 순간 last_seq는 5로 유지되고, 재접속이 6부터 재생한다.
    assert script.requests[1].full_url.endswith("?since=5")
    assert [item.seq for item in items[2:]] == [6, 7]  # type: ignore[union-attr]


def test_replay_unavailable_is_raised_not_retried() -> None:
    script = Scripted(
        _http_error(409, {"error": {"code": "event_replay_unavailable", "message": "pruned"}})
    )
    stream = EventStream("http://x", "t", since=3, opener=script, sleeper=lambda _: None)

    with pytest.raises(ReplayUnavailable) as excinfo:
        next(stream.iter_events())
    assert excinfo.value.since == 3
    assert len(script.requests) == 1


def test_unauthorized_is_fatal() -> None:
    script = Scripted(_http_error(401, {"error": {"code": "unauthorized"}}))
    stream = EventStream("http://x", "t", since=3, opener=script, sleeper=lambda _: None)

    with pytest.raises(BackendError) as excinfo:
        next(stream.iter_events())
    assert excinfo.value.code == "unauthorized"
    assert len(script.requests) == 1


def test_transient_failure_retries_with_backoff() -> None:
    sleeps: list[float] = []
    script = Scripted(urllib.error.URLError("boom"), _frame(1))
    stream = EventStream("http://x", "t", since=0, opener=script, sleeper=sleeps.append)

    assert next(stream.iter_events()).seq == 1  # type: ignore[union-attr]
    assert sleeps == [1.0]
    assert len(script.requests) == 2


def test_backoff_grows_and_resets() -> None:
    sleeps: list[float] = []
    script = Scripted(
        urllib.error.URLError("a"),
        urllib.error.URLError("b"),
        _frame(1),
        StopTest(),
    )
    stream = EventStream("http://x", "t", since=0, opener=script, sleeper=sleeps.append)

    with pytest.raises(StopTest):
        for _ in stream.iter_events():
            pass
    assert sleeps == [1.0, 2.0]


def test_resume_from_head_after_replay_unavailable() -> None:
    stream = EventStream("http://x", "t", since=3, opener=Scripted())
    stream.resume_from(6041)
    assert stream.last_seq == 6041
