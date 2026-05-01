from __future__ import annotations

from pathlib import Path

import pytest

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.relay import RelayError, SmsRelay, build_relay


def _settings(
    *,
    state_dir: Path,
    bot_token: str = "test-token",
    chat_id: str = "-100200300",
) -> Settings:
    return Settings(
        state_dir=state_dir,
        telegram_bot_token=bot_token,
        telegram_chat_id=chat_id,
    )


def test_build_relay_returns_relay(tmp_path: Path) -> None:
    settings = _settings(state_dir=tmp_path / "state")
    metrics = MetricsRegistry()

    relay = build_relay(settings, metrics)

    assert isinstance(relay, SmsRelay)
    assert relay.telegram_client.bot_token == settings.telegram_bot_token
    assert relay.telegram_client.chat_id == settings.telegram_chat_id


def test_build_relay_raises_when_token_empty(tmp_path: Path) -> None:
    settings = _settings(state_dir=tmp_path / "state", bot_token="")
    metrics = MetricsRegistry()

    with pytest.raises(RelayError, match="telegram_bot_token"):
        build_relay(settings, metrics)


def test_build_relay_raises_when_chat_id_empty(tmp_path: Path) -> None:
    settings = _settings(state_dir=tmp_path / "state", chat_id="")
    metrics = MetricsRegistry()

    with pytest.raises(RelayError, match="telegram_chat_id"):
        build_relay(settings, metrics)
