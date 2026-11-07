"""백엔드 API 클라이언트와 오류 타입."""

from .client import BackendClient, SendOutcome
from .errors import BackendError, BackendUnavailable

__all__ = ["BackendClient", "SendOutcome", "BackendError", "BackendUnavailable"]
