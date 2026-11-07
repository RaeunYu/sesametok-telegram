from __future__ import annotations

import pytest

from fake_backend import FakeBackend
from sesametok_tg.backend import BackendClient


@pytest.fixture
def fake() -> FakeBackend:
    with FakeBackend() as server:
        yield server


@pytest.fixture
def client(fake: FakeBackend) -> BackendClient:
    return BackendClient(fake.url, fake.token, timeout=5.0, send_timeout=5.0)
