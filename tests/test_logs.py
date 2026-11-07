from __future__ import annotations

import logging

from sesametok_tg.logs import SecretFilter, redact

BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def test_redacts_bot_token_pattern() -> None:
    assert BOT_TOKEN not in redact(f"봇 토큰 {BOT_TOKEN} 을 썼다")


def test_redacts_bearer_header() -> None:
    assert "abc123def456" not in redact("Authorization: Bearer abc123def456")


def test_redacts_registered_secret_even_without_pattern() -> None:
    assert "s3cr3t-value" not in redact("값 s3cr3t-value 끝", ("s3cr3t-value",))


def test_filter_scrubs_log_record() -> None:
    record = logging.LogRecord("sesametok", logging.INFO, __file__, 1, "인증 %s", (BOT_TOKEN,), None)
    SecretFilter((BOT_TOKEN,)).filter(record)
    assert BOT_TOKEN not in record.getMessage()


def test_filter_keeps_ordinary_messages() -> None:
    record = logging.LogRecord("sesametok", logging.INFO, __file__, 1, "방 3개, 메시지 12건", (), None)
    SecretFilter(()).filter(record)
    assert record.getMessage() == "방 3개, 메시지 12건"
