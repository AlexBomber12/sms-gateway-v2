from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import ModemError, ModemManagerClient
from sms_gateway_v2.modem.models import RegistrationState, SignalQuality
from sms_gateway_v2.relay.watchdog import ModemWatchdog


def _signal(percent: int, *, recent: bool = True) -> SignalQuality:
    return SignalQuality(percent=percent, recent=recent)


_DEFAULT_SIGNAL = _signal(72)


def _set_health_reads(
    modem_client: MagicMock,
    *,
    signal: SignalQuality = _DEFAULT_SIGNAL,
    registration: RegistrationState = RegistrationState.ROAMING,
    operator: str | None = "vodafone IT",
) -> None:
    modem_client.get_signal_quality.return_value = signal
    modem_client.get_registration_state.return_value = registration
    modem_client.get_operator_name.return_value = operator


@pytest.fixture
def modem_client() -> MagicMock:
    client = MagicMock(spec=ModemManagerClient)
    client.get_signal_quality = AsyncMock(return_value=_DEFAULT_SIGNAL)
    client.get_registration_state = AsyncMock(return_value=RegistrationState.ROAMING)
    client.get_operator_name = AsyncMock(return_value="vodafone IT")
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
    _set_health_reads(
        modem_client,
        signal=_signal(72),
        registration=RegistrationState.ROAMING,
    )

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
    _set_health_reads(modem_client, signal=_signal(0))

    for _ in range(3):
        await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert watchdog._consecutive_zero_signal_polls == 0


async def test_non_zero_signal_resets_consecutive_zero_counter(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0))
    await watchdog._poll_once()
    await watchdog._poll_once()
    assert watchdog._consecutive_zero_signal_polls == 2

    _set_health_reads(modem_client, signal=_signal(45))
    await watchdog._poll_once()

    assert watchdog._consecutive_zero_signal_polls == 0
    modem_client.reset.assert_not_awaited()


async def test_bad_state_held_longer_than_threshold_triggers_reset(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(
        modem_client,
        signal=_signal(80),
        registration=RegistrationState.SEARCHING,
    )
    watchdog._bad_state_since = datetime.now(UTC) - timedelta(minutes=15)

    await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert watchdog._bad_state_since is None


async def test_clearing_bad_state_cancels_pending_trigger(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    _set_health_reads(
        modem_client,
        signal=_signal(80),
        registration=RegistrationState.HOME,
    )
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


async def test_poll_failure_reports_modem_health_unavailable(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    modem_client.get_signal_quality.side_effect = ModemError("boom")
    watchdog = ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=3,
        bad_state_minutes=10,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [("unavailable", None, None, "unknown")]


async def test_run_loops_until_stop_called(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    _set_health_reads(modem_client, signal=_signal(50))
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
    _set_health_reads(
        modem_client,
        signal=_signal(80),
        registration=RegistrationState.DENIED,
    )

    await watchdog._poll_once()

    assert watchdog._bad_state_since is not None
    modem_client.reset.assert_not_awaited()


async def test_reset_failure_does_not_terminate_watchdog_loop(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0))
    modem_client.reset.side_effect = ModemError("reset boom")

    for _ in range(3):
        await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert watchdog._consecutive_zero_signal_polls == 0
    assert metrics.registry.get_sample_value("modem_resets_total") == 0.0


async def test_stale_zero_signal_does_not_increment_zero_counter(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0, recent=False))

    for _ in range(5):
        await watchdog._poll_once()

    assert watchdog._consecutive_zero_signal_polls == 0
    modem_client.reset.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_resets_total") == 0.0
    assert metrics.registry.get_sample_value("modem_signal_percent") == 0.0


async def test_operator_name_failure_does_not_skip_health_checks(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), registration=RegistrationState.ROAMING)
    modem_client.get_operator_name.side_effect = ModemError("operator boom")

    for _ in range(3):
        await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    assert watchdog._consecutive_zero_signal_polls == 0
    assert metrics.registry.get_sample_value("modem_signal_percent") == 0.0
    assert (
        metrics.registry.get_sample_value(
            "modem_state",
            labels={"state": RegistrationState.ROAMING.value},
        )
        == 1.0
    )


async def test_stale_signal_preserves_existing_zero_counter(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0))
    await watchdog._poll_once()
    await watchdog._poll_once()
    assert watchdog._consecutive_zero_signal_polls == 2

    _set_health_reads(modem_client, signal=_signal(45, recent=False))
    await watchdog._poll_once()

    assert watchdog._consecutive_zero_signal_polls == 2
    modem_client.reset.assert_not_awaited()


async def test_reset_failure_allows_subsequent_reset_attempts(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0))
    modem_client.reset.side_effect = [ModemError("transient"), None]

    for _ in range(6):
        await watchdog._poll_once()

    assert modem_client.reset.await_count == 2
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0


async def test_watchdog_updates_modem_fields_on_state(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(
        modem_client,
        signal=_signal(83),
        registration=RegistrationState.HOME,
        operator="vodafone IT",
    )
    watchdog = ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=3,
        bad_state_minutes=10,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [("registered", 83, "vodafone IT", "home")]
    modem_client.get_operator_name.assert_awaited_once_with()


async def test_watchdog_reports_registration_issue_as_modem_state(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(
        modem_client,
        signal=_signal(83),
        registration=RegistrationState.SEARCHING,
        operator="vodafone IT",
    )
    watchdog = ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=3,
        bad_state_minutes=10,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [("searching", 83, "vodafone IT", "searching")]


@pytest.mark.parametrize(
    ("registration", "expected_state"),
    [
        (RegistrationState.IDLE, "idle"),
        (RegistrationState.EMERGENCY_ONLY, "emergency_only"),
        (RegistrationState.HOME, "registered"),
        (RegistrationState.ROAMING, "registered"),
    ],
)
async def test_watchdog_reports_only_actual_registration_states_as_registered(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    registration: RegistrationState,
    expected_state: str,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(
        modem_client,
        signal=_signal(83),
        registration=registration,
        operator="vodafone IT",
    )
    watchdog = ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=3,
        bad_state_minutes=10,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [(expected_state, 83, "vodafone IT", registration.value)]
