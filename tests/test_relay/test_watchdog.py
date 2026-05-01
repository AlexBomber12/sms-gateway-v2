from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import ModemError, ModemManagerClient
from sms_gateway_v2.modem.models import RegistrationState, SignalQuality
from sms_gateway_v2.relay.watchdog import ModemWatchdog


def _signal(percent: int) -> SignalQuality:
    return SignalQuality(percent=percent, recent=True)


@pytest.fixture
def modem_client() -> MagicMock:
    client = MagicMock(spec=ModemManagerClient)
    client.get_signal_quality = AsyncMock()
    client.get_registration_state = AsyncMock()
    client.reset = AsyncMock()
    return client


@pytest.fixture
def metrics() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture
def watchdog(modem_client: MagicMock, metrics: MetricsRegistry) -> ModemWatchdog:
    return ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=3,
        bad_state_minutes=10,
    )


async def test_poll_once_updates_signal_and_state_gauges(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(72)
    modem_client.get_registration_state.return_value = RegistrationState.ROAMING

    await watchdog._poll_once()

    assert metrics.registry.get_sample_value("modem_signal_percent") == 72.0
    assert (
        metrics.registry.get_sample_value(
            "modem_state",
            labels={"state": RegistrationState.ROAMING.value},
        )
        == 1.0
    )
    assert (
        metrics.registry.get_sample_value(
            "modem_state",
            labels={"state": RegistrationState.HOME.value},
        )
        == 0.0
    )
    assert (
        metrics.registry.get_sample_value(
            "modem_state",
            labels={"state": RegistrationState.SEARCHING.value},
        )
        == 0.0
    )


async def test_consecutive_zero_signal_polls_trigger_reset_exactly_once(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(0)
    modem_client.get_registration_state.return_value = RegistrationState.ROAMING

    for _ in range(3):
        await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert watchdog._consecutive_zero_signal_polls == 0


async def test_non_zero_signal_resets_consecutive_zero_counter(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    modem_client.get_registration_state.return_value = RegistrationState.ROAMING

    modem_client.get_signal_quality.return_value = _signal(0)
    await watchdog._poll_once()
    await watchdog._poll_once()
    assert watchdog._consecutive_zero_signal_polls == 2

    modem_client.get_signal_quality.return_value = _signal(45)
    await watchdog._poll_once()

    assert watchdog._consecutive_zero_signal_polls == 0
    modem_client.reset.assert_not_awaited()


async def test_bad_state_held_longer_than_threshold_triggers_reset(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(80)
    modem_client.get_registration_state.return_value = RegistrationState.SEARCHING
    watchdog._bad_state_since = datetime.now(UTC) - timedelta(minutes=15)

    await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert watchdog._bad_state_since is None


async def test_clearing_bad_state_cancels_pending_trigger(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(80)
    modem_client.get_registration_state.return_value = RegistrationState.HOME
    watchdog._bad_state_since = datetime.now(UTC) - timedelta(minutes=15)

    await watchdog._poll_once()

    assert watchdog._bad_state_since is None
    modem_client.reset.assert_not_awaited()


async def test_poll_failure_does_not_increment_counters_or_reset(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    modem_client.get_signal_quality.side_effect = ModemError("boom")
    watchdog._consecutive_zero_signal_polls = 2

    await watchdog._poll_once()

    assert watchdog._consecutive_zero_signal_polls == 2
    modem_client.reset.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_resets_total") == 0.0


async def test_run_loops_until_stop_called(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(50)
    modem_client.get_registration_state.return_value = RegistrationState.ROAMING
    watchdog._interval_seconds = 0.01
    poll_calls = 0

    async def stopping_poll() -> None:
        nonlocal poll_calls
        poll_calls += 1
        watchdog.stop()

    watchdog._poll_once = stopping_poll  # type: ignore[method-assign]

    await asyncio.wait_for(watchdog.run(), timeout=1.0)

    assert poll_calls == 1


async def test_first_bad_state_sets_since_marker_without_resetting(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    modem_client.get_signal_quality.return_value = _signal(80)
    modem_client.get_registration_state.return_value = RegistrationState.DENIED

    await watchdog._poll_once()

    assert watchdog._bad_state_since is not None
    modem_client.reset.assert_not_awaited()
