"""로깅 설정과 비밀 마스킹.

이 프로젝트는 **메시지 본문을 로그에 남기지 않는다.** 그 위에 한 겹 더, 토큰처럼 로그에
섞여 들어올 수 있는 값을 정규식·등록 목록으로 지운다. 남겨도 되는 것은 개수·id·문자 수·상태다.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_REDACTED = "<redacted>"

_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{6,})")
_TOKEN_KV = re.compile(r"(?i)\b(token|bot_token|api_token)\b(\s*[=:]\s*)(\S+)")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """등록된 비밀값과 토큰 형태의 문자열을 지운다."""

    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    text = _BOT_TOKEN.sub(_REDACTED, text)
    text = _BEARER.sub(r"\1" + _REDACTED, text)
    text = _TOKEN_KV.sub(r"\1\2" + _REDACTED, text)
    return text


class SecretFilter(logging.Filter):
    """레코드가 핸들러에 도달하기 전에 비밀값을 지운다."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 로깅이 애플리케이션을 죽이면 안 된다
            return True
        record.msg = redact(message, self._secrets)
        record.args = ()
        return True


def configure_logging(
    log_dir: Path,
    *,
    secrets: tuple[str, ...] = (),
    level: str = "INFO",
    console: bool = True,
) -> None:
    """`<log_dir>/client.log`(회전)와 선택적 콘솔로 로그를 보낸다."""

    directory = Path(log_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    filter_ = SecretFilter(secrets)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        directory / "client.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(filter_)

    handlers: list[logging.Handler] = [file_handler]
    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(filter_)
        handlers.append(stream_handler)

    for handler in handlers:
        root.addHandler(handler)
