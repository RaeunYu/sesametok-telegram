from __future__ import annotations

from pathlib import Path

import pytest
from fake_backend import FakeBackend
from fake_telegram import FakeTelegram

from sesametok_tg.backend import BackendClient
from sesametok_tg.store import Store
from sesametok_tg.telegram.api import TelegramBot


@pytest.fixture
def fake() -> FakeBackend:
    with FakeBackend() as server:
        yield server


@pytest.fixture
def client(fake: FakeBackend) -> BackendClient:
    return BackendClient(fake.url, fake.token, timeout=5.0, send_timeout=5.0)


@pytest.fixture
def telegram() -> FakeTelegram:
    with FakeTelegram() as server:
        yield server


@pytest.fixture
def bot(telegram: FakeTelegram) -> TelegramBot:
    return TelegramBot(telegram.token, base_url=telegram.url, timeout=5.0)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "state.sqlite3") as opened:
        yield opened
