"""설정 로딩. 우선순위는 **환경변수 > 설정 파일 > 기본값**이다.

설정 파일은 `~/.config/sesametok-telegram/config.toml`(0600)이며, `setup` 명령이 만든다.
토큰은 이 모듈에서만 다루고 절대 로그에 남기지 않는다(redaction은 `logs.py`).

백엔드 URL은 **loopback만 허용**한다. 백엔드 인수인계 문서의 불변식(`127.0.0.1`/UDS 전용)을
설정 단계에서 강제해, 실수로 외부 주소를 넣는 사고를 막는다.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CONFIG_PATH = "~/.config/sesametok-telegram/config.toml"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8756"
DEFAULT_BACKEND_TOKEN_PATH = "~/Library/Application Support/sesametok-backend/api_token"
DEFAULT_DATA_DIR = "~/Library/Application Support/sesametok-telegram"
DEFAULT_LOG_DIR = "~/Library/Logs/sesametok-telegram"

# 백엔드는 발신 결과를 최대 90초 기다리므로 클라이언트 타임아웃은 그보다 길어야 한다.
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_SEND_TIMEOUT = 120.0

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


class ConfigError(Exception):
    """설정이 없거나 잘못되었다. 메시지는 사용자가 바로 고칠 수 있게 쓴다."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_user_id: int
    backend_base_url: str
    backend_token_path: Path
    data_dir: Path
    log_dir: Path
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    send_timeout: float = DEFAULT_SEND_TIMEOUT
    config_path: Path = Path(DEFAULT_CONFIG_PATH).expanduser()


def load_settings(
    config_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Settings:
    """설정을 읽고 검증한다. 값이 없으면 `ConfigError`로 무엇을 고쳐야 하는지 알려준다."""

    environ = os.environ if env is None else env
    raw_path = environ.get("SESAMETOK_TG_CONFIG") or config_path or DEFAULT_CONFIG_PATH
    path = Path(raw_path).expanduser()
    data = _read_toml(path)

    bot_token = environ.get("SESAMETOK_TG_BOT_TOKEN") or data.get("bot_token") or ""
    if not isinstance(bot_token, str) or not bot_token.strip():
        raise ConfigError(
            "봇 토큰이 없습니다. `sesametok-telegram setup`을 실행하거나 "
            f"{path}에 bot_token을 넣으세요."
        )

    raw_owner = environ.get("SESAMETOK_TG_OWNER_USER_ID", data.get("owner_user_id"))
    owner_user_id = _parse_owner(raw_owner)

    raw_backend = (
        environ.get("SESAMETOK_TG_BACKEND_URL")
        or data.get("backend_base_url")
        or DEFAULT_BACKEND_URL
    )
    backend_base_url = _validate_backend_url(str(raw_backend))

    backend_token_path = Path(
        environ.get("SESAMETOK_TG_BACKEND_TOKEN_PATH")
        or data.get("backend_token_path")
        or DEFAULT_BACKEND_TOKEN_PATH
    ).expanduser()

    data_dir = Path(
        environ.get("SESAMETOK_TG_DATA_DIR") or data.get("data_dir") or DEFAULT_DATA_DIR
    ).expanduser()
    log_dir = Path(
        environ.get("SESAMETOK_TG_LOG_DIR") or data.get("log_dir") or DEFAULT_LOG_DIR
    ).expanduser()

    request_timeout = _parse_timeout(
        environ.get("SESAMETOK_TG_REQUEST_TIMEOUT") or data.get("request_timeout"),
        DEFAULT_REQUEST_TIMEOUT,
        "request_timeout",
    )
    send_timeout = _parse_timeout(
        environ.get("SESAMETOK_TG_SEND_TIMEOUT") or data.get("send_timeout"),
        DEFAULT_SEND_TIMEOUT,
        "send_timeout",
    )

    return Settings(
        bot_token=bot_token.strip(),
        owner_user_id=owner_user_id,
        backend_base_url=backend_base_url,
        backend_token_path=backend_token_path,
        data_dir=data_dir,
        log_dir=log_dir,
        request_timeout=request_timeout,
        send_timeout=send_timeout,
        config_path=path,
    )


def read_backend_token(path: Path) -> str:
    """백엔드 API 토큰을 파일에서 읽는다. 값은 로그·예외 메시지에 넣지 않는다."""

    try:
        token = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"백엔드 토큰 파일이 없습니다: {path}\n"
            "백엔드를 한 번 실행하거나 `sesametok-backend token`으로 토큰을 확인하세요."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"백엔드 토큰 파일을 읽을 수 없습니다: {path} ({exc.strerror})") from exc
    if not token:
        raise ConfigError(f"백엔드 토큰 파일이 비어 있습니다: {path}")
    return token


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"설정 파일 형식이 올바르지 않습니다: {path} ({exc})") from exc


def _parse_owner(raw: object) -> int:
    if raw is None or raw == "":
        raise ConfigError(
            "소유자 user_id가 없습니다. `setup`을 실행해 봇에게 /start를 보내고 "
            "화면에 표시된 id를 확인하세요."
        )
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"owner_user_id는 숫자여야 합니다: {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"owner_user_id가 올바르지 않습니다: {raw!r}")
    return value


def _validate_backend_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme != "http":
        raise ConfigError(f"백엔드 URL은 http여야 합니다: {raw!r}")
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ConfigError(
            f"백엔드 URL은 loopback만 허용합니다(127.0.0.1, localhost, ::1): {raw!r}"
        )
    if parts.port is None:
        raise ConfigError(f"백엔드 URL에 포트를 명시하세요(기본 8756): {raw!r}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError(f"백엔드 URL에는 경로·쿼리를 넣지 마세요: {raw!r}")
    return f"http://{parts.netloc}"


def _parse_timeout(raw: object, default: float, name: str) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}은 숫자(초)여야 합니다: {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name}은 0보다 커야 합니다: {raw!r}")
    return value
