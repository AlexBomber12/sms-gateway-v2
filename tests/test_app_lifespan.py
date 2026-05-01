from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sms_gateway_v2 import app as app_module
from sms_gateway_v2.relay import RelayError, SmsRelay
from sms_gateway_v2.telegram import TelegramClient


@pytest.fixture
def relay_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100200300")
    yield


def test_relay_disabled_skips_lifespan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "false")

    app = app_module.create_app()
    with TestClient(app) as client:
        assert not hasattr(app.state, "relay")
        response = client.get("/healthz")
    assert response.status_code == 200


def test_relay_enabled_calls_start_and_stop(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    fake_build_relay = MagicMock(return_value=relay)
    monkeypatch.setattr(app_module, "build_relay", fake_build_relay)

    app = app_module.create_app()
    with TestClient(app):
        pass

    fake_build_relay.assert_called_once()
    telegram_client.__aenter__.assert_awaited_once()
    telegram_client.__aexit__.assert_awaited_once()
    relay.start.assert_awaited_once()
    relay.stop.assert_awaited_once()


def test_relay_enabled_missing_credentials_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    app = app_module.create_app()
    with pytest.raises(RelayError, match="telegram_bot_token"), TestClient(app):
        pass


def test_relay_start_failure_releases_telegram_client(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock(side_effect=RelayError("boom"))
    relay.stop = AsyncMock()

    monkeypatch.setattr(app_module, "build_relay", MagicMock(return_value=relay))

    app = app_module.create_app()
    with pytest.raises(RelayError, match="boom"), TestClient(app):
        pass

    telegram_client.__aenter__.assert_awaited_once()
    telegram_client.__aexit__.assert_awaited_once()
    relay.stop.assert_not_awaited()
