from __future__ import annotations

import pytest
from fake_telegram import ErrorSpec, FakeTelegram

from sesametok_tg.telegram.api import TelegramBot, TelegramError


def test_send_message_returns_result(bot: TelegramBot, telegram: FakeTelegram) -> None:
    result = bot.send_message(-100, "[나]: 안녕")
    assert isinstance(result.get("message_id"), int)
    sent = telegram.calls_of("sendMessage")[0]
    assert sent["chat_id"] == -100
    assert sent["text"] == "[나]: 안녕"


def test_token_is_used_in_path(bot: TelegramBot, telegram: FakeTelegram) -> None:
    bot.get_chat_member_count(-100)
    assert telegram.calls_of("getChatMemberCount")[0]["chat_id"] == -100


def test_api_error_is_mapped(bot: TelegramBot, telegram: FakeTelegram) -> None:
    telegram.failures["sendMessage"] = ErrorSpec(400, "Bad Request: chat not found")
    with pytest.raises(TelegramError) as excinfo:
        bot.send_message(-100, "x")
    assert excinfo.value.code == 400
    assert excinfo.value.retryable is False


def test_rate_limit_exposes_retry_after(bot: TelegramBot, telegram: FakeTelegram) -> None:
    telegram.failures["sendMessage"] = ErrorSpec(429, "Too Many Requests", retry_after=7)
    with pytest.raises(TelegramError) as excinfo:
        bot.send_message(-100, "x")
    assert excinfo.value.retry_after == 7
    assert excinfo.value.retryable is True


def test_transport_error_does_not_leak_token() -> None:
    secret = "123456789:SUPERSECRETTOKEN"
    bot = TelegramBot(secret, base_url="http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(TelegramError) as excinfo:
        bot.send_message(-100, "x")
    assert secret not in str(excinfo.value)
    assert excinfo.value.code is None


def test_get_updates_passes_offset(bot: TelegramBot, telegram: FakeTelegram) -> None:
    telegram.updates = [{"update_id": 10, "message": {}}]
    batch = bot.get_updates(offset=11, poll_timeout=0)
    assert telegram.calls_of("getUpdates")[0]["offset"] == 11
    assert batch == []


def test_get_updates_returns_pending(bot: TelegramBot, telegram: FakeTelegram) -> None:
    telegram.updates = [{"update_id": 12, "message": {}}]
    batch = bot.get_updates(offset=10, poll_timeout=0)
    assert [update["update_id"] for update in batch] == [12]


def test_rejects_oversized_text(bot: TelegramBot) -> None:
    with pytest.raises(TelegramError):
        bot.send_message(-100, "가" * 5000)
