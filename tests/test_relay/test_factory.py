from __future__ import annotations

from pathlib import Path

import pytest

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry, QueueGaugeUpdater
from sms_gateway_v2.relay import (
    CleanupScheduler,
    ModemWatchdog,
    RelayError,
    SmsRelay,
    build_relay,
)


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


def test_build_relay_returns_relay_gauge_updater_and_watchdog(tmp_path: Path) -> None:
    settings = _settings(state_dir=tmp_path / "state")
    metrics = MetricsRegistry()

    relay, gauge_updater, watchdog, cleanup_scheduler = build_relay(settings, metrics)

    assert isinstance(relay, SmsRelay)
    assert isinstance(gauge_updater, QueueGaugeUpdater)
    assert isinstance(watchdog, ModemWatchdog)
    assert isinstance(cleanup_scheduler, CleanupScheduler)
    assert relay.telegram_client.bot_token == settings.telegram_bot_token
    assert relay.telegram_client.chat_id == settings.telegram_chat_id


def test_build_relay_uses_configured_gauge_interval(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        queue_gauge_interval_seconds=7.5,
    )
    metrics = MetricsRegistry()

    _, gauge_updater, _, _ = build_relay(settings, metrics)

    assert gauge_updater._interval_seconds == 7.5


def test_build_relay_uses_configured_watchdog_settings(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        modem_watchdog_interval_seconds=15.0,
        modem_watchdog_signal_zero_threshold=2,
        modem_watchdog_bad_state_minutes=4,
    )
    metrics = MetricsRegistry()

    _, _, watchdog, _ = build_relay(settings, metrics)

    assert watchdog._interval_seconds == 15.0
    assert watchdog._signal_zero_threshold == 2
    assert watchdog._bad_state_minutes == 4


def test_build_relay_uses_configured_cleanup_settings(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        queue_sent_retention_days=7,
        queue_failed_retention_days=14,
        cleanup_interval_seconds=120.0,
    )
    metrics = MetricsRegistry()

    _, _, _, cleanup_scheduler = build_relay(settings, metrics)

    assert cleanup_scheduler._sent_retention_days == 7
    assert cleanup_scheduler._failed_retention_days == 14
    assert cleanup_scheduler._interval_seconds == 120.0


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
