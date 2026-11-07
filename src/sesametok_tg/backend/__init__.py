"""백엔드 API 클라이언트, SSE 구독, 오류 타입."""

from .client import BackendClient, SendOutcome
from .errors import BackendError, BackendUnavailable
from .stream import Event, EventStream, Gap, ReplayUnavailable

__all__ = [
    "BackendClient",
    "SendOutcome",
    "BackendError",
    "BackendUnavailable",
    "Event",
    "EventStream",
    "Gap",
    "ReplayUnavailable",
]
