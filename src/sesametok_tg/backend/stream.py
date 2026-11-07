"""SSE 구독(`GET /v1/events`).

백엔드는 `id:`/`event:`/`data:` 프레임을 보내고, 15초마다 `: ping` 주석을 보낸다. 이 클라이언트가
지켜야 하는 계약은 세 가지다.

- **재개**: `?since=` 또는 `Last-Event-ID`로 이어 붙인다. 이벤트는 아카이브에서 재생되므로
  `since`를 "마지막으로 **처리한** seq"로 두면 끊긴 구간이 그대로 메워진다.
- **갭**: `seq`가 이어지지 않으면 그 자리에서 연결을 끊고 마지막 seq로 다시 붙는다. 버스 큐
  (구독자당 2,000)에서 버린 이벤트는 재생으로 복구된다. 재생조차 불가능한 경우(prune 이후)에만
  `ReplayUnavailable`이 올라오고, 호출자는 헤드에서 다시 시작해야 한다.
- **에러**: 401 같은 영구 오류는 재시도하지 않고 즉시 올린다. 그 밖의 연결 실패는 지수 백오프.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .errors import BackendError

logger = logging.getLogger(__name__)

DEFAULT_READ_TIMEOUT = 45.0  # 서버 keep-alive가 15초이므로 넉넉히
DEFAULT_BACKOFF_INITIAL = 1.0
DEFAULT_BACKOFF_MAX = 30.0


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: str | None = None


@dataclass(frozen=True)
class Gap:
    """`seq`가 이어지지 않았다 = 이벤트 버스 큐 유실. 재접속 재생으로 메운다."""

    expected_seq: int
    received_seq: int


class ReplayUnavailable(BackendError):
    """이벤트가 prune 되어 요청한 `since`부터 재생할 수 없다(409)."""

    def __init__(self, since: int | None, message: str = "") -> None:
        super().__init__("event_replay_unavailable", 409, message, retryable=False)
        self.since = since


Opener = Callable[[urllib.request.Request, float], Any]


class EventStream:
    """SSE를 구독하며 `Event`와 `Gap`을 순서대로 내놓는다.

    `opener`와 `sleeper`를 주입할 수 있어 네트워크 없이 재연결·갭 시나리오를 테스트한다.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        since: int | None = None,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        opener: Opener | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.last_seq = since
        self._read_timeout = read_timeout
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff = backoff_initial
        self._opener: Opener = opener or _urlopen
        self._sleeper = sleeper
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def resume_from(self, seq: int | None) -> None:
        """409를 받은 뒤 헤드에서 다시 시작할 때 쓴다."""

        self.last_seq = seq
        self._backoff = self._backoff_initial

    def iter_events(self) -> Iterator[Event | Gap]:
        while not self._closed:
            try:
                yield from self._read_once()
            except ReplayUnavailable:
                raise
            except BackendError:
                raise
            except (urllib.error.URLError, OSError) as exc:
                delay = self._backoff
                self._backoff = min(self._backoff * 2, self._backoff_max)
                logger.warning(
                    "이벤트 스트림이 끊겼습니다(since=%s). %.0f초 뒤 재접속합니다: %s",
                    self.last_seq,
                    delay,
                    type(exc).__name__,
                )
                self._sleeper(delay)

    # -- 내부 ---------------------------------------------------------------

    def _read_once(self) -> Iterator[Event | Gap]:
        response = self._open()
        try:
            for frame in iter_frames(iter_lines(response)):
                event = parse_frame(frame)
                if event is None:
                    continue
                if self.last_seq is not None and event.seq != self.last_seq + 1:
                    gap = Gap(expected_seq=self.last_seq + 1, received_seq=event.seq)
                    logger.warning("seq 갭: %d을 기대했는데 %d이 왔습니다", gap.expected_seq, gap.received_seq)
                    yield gap
                    return  # 끊고 마지막 seq로 재접속하면 재생이 메운다
                self.last_seq = event.seq
                self._backoff = self._backoff_initial
                yield event
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _open(self) -> Any:
        url = f"{self.base_url}/v1/events"
        if self.last_seq is not None:
            url = f"{url}?since={self.last_seq}"
        headers = {"Accept": "text/event-stream"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self.last_seq is not None:
            headers["Last-Event-ID"] = str(self.last_seq)
        request = urllib.request.Request(url, headers=headers)
        try:
            return self._opener(request, self._read_timeout)
        except urllib.error.HTTPError as exc:
            raise _translate(exc, self.last_seq) from None


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _translate(exc: urllib.error.HTTPError, since: int | None) -> BackendError:
    error: dict[str, Any] = {}
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
    except (UnicodeDecodeError, ValueError, OSError):
        error = {}
    code = str(error.get("code") or f"http_{exc.code}")
    message = str(error.get("message") or "")
    if code == "event_replay_unavailable":
        return ReplayUnavailable(since, message)
    return BackendError(code, exc.code, message, retryable=bool(error.get("retryable", False)))


def iter_lines(response: Any) -> Iterator[str]:
    """응답을 줄 단위로 읽는다(`bytes`/`str` 모두 지원)."""

    for raw in response:
        if isinstance(raw, bytes):
            yield raw.decode("utf-8", "replace").rstrip("\r\n")
        else:
            yield str(raw).rstrip("\r\n")


def iter_frames(lines: Iterator[str]) -> Iterator[list[str]]:
    """빈 줄로 끝나는 프레임을 모은다. `:`로 시작하는 주석(`: ping`)은 버린다."""

    frame: list[str] = []
    for line in lines:
        if line == "":
            if frame:
                yield frame
                frame = []
            continue
        if line.startswith(":"):
            continue
        frame.append(line)
    if frame:
        yield frame


def parse_frame(lines: list[str]) -> Event | None:
    """SSE 프레임 → `Event`. 해석할 수 없으면 `None`(본문은 로그에 남기지 않는다)."""

    data_lines: list[str] = []
    kind: str | None = None
    event_id: int | None = None

    for line in lines:
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            kind = value or None
        elif field == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = None

    if not data_lines:
        return None

    try:
        body = json.loads("\n".join(data_lines))
    except ValueError:
        logger.warning("SSE data를 JSON으로 해석하지 못했습니다")
        return None
    if not isinstance(body, dict):
        return None

    seq = event_id if event_id is not None else body.get("seq")
    if not isinstance(seq, int):
        logger.warning("SSE 프레임에 seq가 없습니다")
        return None

    payload = body.get("payload")
    return Event(
        seq=seq,
        kind=str(kind or body.get("kind") or "message.created"),
        payload=payload if isinstance(payload, dict) else {},
        created_at=body.get("created_at"),
    )
