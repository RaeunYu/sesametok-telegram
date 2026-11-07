from __future__ import annotations

from pathlib import Path

import pytest

from sesametok_tg.config import ConfigError, load_settings, read_backend_token


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_from_toml(tmp_path: Path) -> None:
    path = _config(tmp_path, 'bot_token = "123:abc"\nowner_user_id = 42\n')
    settings = load_settings(path, env={})
    assert settings.bot_token == "123:abc"
    assert settings.owner_user_id == 42
    assert settings.backend_base_url == "http://127.0.0.1:8756"


def test_env_overrides_file(tmp_path: Path) -> None:
    path = _config(tmp_path, 'bot_token = "file-token"\nowner_user_id = 1\n')
    settings = load_settings(
        path,
        env={"SESAMETOK_TG_BOT_TOKEN": "env-token", "SESAMETOK_TG_OWNER_USER_ID": "7"},
    )
    assert settings.bot_token == "env-token"
    assert settings.owner_user_id == 7


def test_missing_bot_token_explains_the_fix() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings("/nonexistent/config.toml", env={})
    assert "setup" in str(excinfo.value)


def test_rejects_non_loopback_backend() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            None,
            env={
                "SESAMETOK_TG_BOT_TOKEN": "t",
                "SESAMETOK_TG_OWNER_USER_ID": "1",
                "SESAMETOK_TG_BACKEND_URL": "http://example.com:8756",
            },
        )
    assert "loopback" in str(excinfo.value)


def test_rejects_backend_url_without_port() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            None,
            env={
                "SESAMETOK_TG_BOT_TOKEN": "t",
                "SESAMETOK_TG_OWNER_USER_ID": "1",
                "SESAMETOK_TG_BACKEND_URL": "http://127.0.0.1",
            },
        )
    assert "포트" in str(excinfo.value)


def test_owner_id_must_be_numeric() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            None,
            env={"SESAMETOK_TG_BOT_TOKEN": "t", "SESAMETOK_TG_OWNER_USER_ID": "abc"},
        )
    assert "숫자" in str(excinfo.value)


def test_read_backend_token_strips_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "api_token"
    path.write_text("secret-token\n", encoding="utf-8")
    assert read_backend_token(path) == "secret-token"


def test_read_backend_token_missing_gives_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        read_backend_token(tmp_path / "nope")
    assert "sesametok-backend token" in str(excinfo.value)
