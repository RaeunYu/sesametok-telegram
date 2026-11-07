"""백엔드 호출에서 나오는 오류.

오류 응답은 항상 `{"error": {"code", "message", "retryable"}}` 형태다. 이 프로젝트는
`retryable`을 **재시도 판단의 근거로 쓰지 않는다**(429와 `unconfirmed`도 false다). 판단은
`docs/IMPLEMENTATION_PLAN.md` §5의 오류 매핑표를 따른다.
"""

from __future__ import annotations


class BackendError(Exception):
    """백엔드가 오류 응답을 돌려줬거나 응답을 해석할 수 없다."""

    def __init__(
        self,
        code: str,
        status: int | str | None,
        message: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        detail = f" ({self.status})" if self.status is not None else ""
        return f"{self.code}{detail}: {self.message}"


class BackendUnavailable(BackendError):
    """연결 실패·타임아웃처럼 응답 자체를 받지 못한 경우."""

    def __init__(self, message: str) -> None:
        super().__init__("unavailable", None, message, retryable=True)
