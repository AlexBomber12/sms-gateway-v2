from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import sms_gateway_v2.relay.watchdog as watchdog_module
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
    modem_state: str = "registered",
    operator: str | None = "vodafone IT",
) -> None:
    modem_client.get_signal_quality.return_value = signal
    modem_client.get_registration_state.return_value = registration
    modem_client.get_modem_state.return_value = modem_state
    modem_client.get_operator_name.return_value = operator


def _make_watchdog(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    *,
    signal_zero_threshold: int = 3,
    bad_state_minutes: int = 10,
    enable_cooldown_seconds: float = 120.0,
    enable_frozen_cooldown_seconds: float = 1800.0,
    modem_health_callback: (Callable[[str, int | None, str | None, str], None] | None) = None,
) -> ModemWatchdog:
    return ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=60.0,
        signal_zero_threshold=signal_zero_threshold,
        bad_state_minutes=bad_state_minutes,
        modem_health_callback=modem_health_callback,
        enable_cooldown_seconds=enable_cooldown_seconds,
        enable_frozen_cooldown_seconds=enable_frozen_cooldown_seconds,
    )


@pytest.fixture
def modem_client() -> MagicMock:
    client = MagicMock(spec=ModemManagerClient)
    client.get_signal_quality = AsyncMock(return_value=_DEFAULT_SIGNAL)
    client.get_registration_state = AsyncMock(return_value=RegistrationState.ROAMING)
    client.get_modem_state = AsyncMock(return_value="registered")
    client.get_operator_name = AsyncMock(return_value="vodafone IT")
    client.reset = AsyncMock()
    client.enable = AsyncMock()
    return client


@pytest.fixture
def metrics() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture
def watchdog(modem_client: MagicMock, metrics: MetricsRegistry) -> ModemWatchdog:
    return _make_watchdog(modem_client, metrics)


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
    modem_client.enable.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert watchdog._consecutive_zero_signal_polls == 0


async def test_watchdog_enables_when_modem_state_disabled(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="disabled")
    captured_logger = MagicMock()
    monkeypatch.setattr(watchdog_module, "logger", captured_logger)
    watchdog = _make_watchdog(modem_client, metrics, signal_zero_threshold=1)

    await watchdog._poll_once()

    modem_client.enable.assert_awaited_once_with()
    modem_client.reset.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_enables_total") == 1.0
    captured_logger.warning.assert_any_call(
        "watchdog_triggering_modem_enable",
        reason="zero_signal",
        modem_state="disabled",
        consecutive_zero_signal_polls=1,
    )


async def test_watchdog_resets_when_modem_state_healthy(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="registered")
    watchdog = _make_watchdog(modem_client, metrics, signal_zero_threshold=1)

    await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    modem_client.enable.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_resets_total") == 1.0
    assert metrics.registry.get_sample_value("modem_enables_total") == 0.0


@pytest.mark.parametrize("modem_state", ["locked", "failed"])
async def test_watchdog_enables_for_locked_and_failed_states(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    modem_state: str,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state=modem_state)
    watchdog = _make_watchdog(modem_client, metrics, signal_zero_threshold=1)

    await watchdog._poll_once()

    modem_client.enable.assert_awaited_once_with()
    modem_client.reset.assert_not_awaited()
    assert metrics.registry.get_sample_value("modem_enables_total") == 1.0


@pytest.mark.parametrize(
    "modem_state",
    ["initializing", "enabling", "disabling", "disconnecting"],
)
async def test_watchdog_does_not_enable_for_transient_states(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    modem_state: str,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state=modem_state)
    watchdog = _make_watchdog(modem_client, metrics, signal_zero_threshold=1)

    await watchdog._poll_once()

    modem_client.reset.assert_awaited_once_with()
    modem_client.enable.assert_not_awaited()


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


@pytest.mark.parametrize(
    "registration",
    [
        RegistrationState.IDLE,
        RegistrationState.EMERGENCY_ONLY,
        RegistrationState.ATTACHED_RLOS,
    ],
)
async def test_non_registered_health_states_do_not_trigger_bad_state_reset(
    watchdog: ModemWatchdog,
    modem_client: MagicMock,
    registration: RegistrationState,
) -> None:
    _set_health_reads(
        modem_client,
        signal=_signal(80),
        registration=registration,
    )
    watchdog._bad_state_since = datetime.now(UTC) - timedelta(minutes=15)

    await watchdog._poll_once()

    assert watchdog._bad_state_since is None
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


async def test_watchdog_enable_respects_cooldown(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="disabled")
    captured_logger = MagicMock()
    monkeypatch.setattr(watchdog_module, "logger", captured_logger)
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        signal_zero_threshold=1,
        enable_cooldown_seconds=120.0,
    )

    await watchdog._poll_once()
    await watchdog._poll_once()

    modem_client.enable.assert_awaited_once_with()
    captured_logger.info.assert_any_call(
        "watchdog_enable_cooldown_skipped",
        modem_state="disabled",
        seconds_remaining=pytest.approx(120.0, abs=1.0),
    )


async def test_watchdog_enable_cooldown_resets_counters_on_skip(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="disabled")
    watchdog = _make_watchdog(modem_client, metrics, signal_zero_threshold=1)
    watchdog._last_enable_attempt_at = datetime.now(UTC)

    await watchdog._poll_once()

    modem_client.enable.assert_not_awaited()
    assert watchdog._consecutive_zero_signal_polls == 0


async def test_watchdog_detects_frozen_modem_on_invalid_transition(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="disabled")
    error = ModemError("org.freedesktop.ModemManager1.Error.Core.Retry: Invalid transition")
    modem_client.enable.side_effect = error
    captured_logger = MagicMock()
    monkeypatch.setattr(watchdog_module, "logger", captured_logger)
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        signal_zero_threshold=1,
        enable_cooldown_seconds=120.0,
        enable_frozen_cooldown_seconds=1800.0,
    )
    before = datetime.now(UTC)

    await watchdog._poll_once()

    modem_client.enable.assert_awaited_once_with()
    captured_logger.error.assert_any_call(
        "watchdog_modem_frozen",
        modem_state="disabled",
        error=str(error),
    )
    assert watchdog._last_enable_attempt_at is not None
    next_attempt_at = watchdog._last_enable_attempt_at + timedelta(
        seconds=watchdog._enable_cooldown_seconds
    )
    assert (next_attempt_at - before).total_seconds() == pytest.approx(1800.0, abs=1.0)
    assert metrics.registry.get_sample_value("modem_enables_total") == 0.0


async def test_watchdog_enable_failure_other_error_uses_normal_cooldown(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_health_reads(modem_client, signal=_signal(0), modem_state="disabled")
    error = ModemError("modem enable timed out")
    modem_client.enable.side_effect = error
    captured_logger = MagicMock()
    monkeypatch.setattr(watchdog_module, "logger", captured_logger)
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        signal_zero_threshold=1,
        enable_cooldown_seconds=120.0,
        enable_frozen_cooldown_seconds=1800.0,
    )
    before = datetime.now(UTC)

    await watchdog._poll_once()

    captured_logger.warning.assert_any_call(
        "watchdog_enable_failed",
        reason="zero_signal",
        modem_state="disabled",
        error=str(error),
    )
    assert watchdog._last_enable_attempt_at is not None
    next_attempt_at = watchdog._last_enable_attempt_at + timedelta(
        seconds=watchdog._enable_cooldown_seconds
    )
    assert (next_attempt_at - before).total_seconds() == pytest.approx(120.0, abs=1.0)
    assert metrics.registry.get_sample_value("modem_enables_total") == 0.0


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


async def test_watchdog_reports_real_modem_state_to_health_callback(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(
        modem_client,
        signal=_signal(83),
        registration=RegistrationState.UNKNOWN,
        modem_state="disabled",
        operator="vodafone IT",
    )
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [("disabled", 83, "vodafone IT", "unknown")]


@pytest.mark.parametrize(
    ("registration", "modem_state"),
    [
        (RegistrationState.IDLE, "enabled"),
        (RegistrationState.EMERGENCY_ONLY, "searching"),
        (RegistrationState.HOME, "registered"),
        (RegistrationState.ROAMING, "registered"),
    ],
)
async def test_watchdog_reports_modem_state_independent_of_registration(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    registration: RegistrationState,
    modem_state: str,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(
        modem_client,
        signal=_signal(83),
        registration=registration,
        modem_state=modem_state,
        operator="vodafone IT",
    )
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    assert captured == [(modem_state, 83, "vodafone IT", registration.value)]


async def test_watchdog_poll_failure_still_marks_unavailable(
    modem_client: MagicMock,
    metrics: MetricsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, int | None, str | None, str]] = []
    _set_health_reads(modem_client, signal=_signal(83), registration=RegistrationState.HOME)
    error = ModemError("state read failed")
    modem_client.get_modem_state.side_effect = error
    captured_logger = MagicMock()
    monkeypatch.setattr(watchdog_module, "logger", captured_logger)
    watchdog = _make_watchdog(
        modem_client,
        metrics,
        modem_health_callback=lambda state, signal_percent, operator, registration: captured.append(
            (state, signal_percent, operator, registration)
        ),
    )

    await watchdog._poll_once()

    captured_logger.warning.assert_any_call("watchdog_poll_failed", error=str(error))
    assert captured == [("unavailable", None, None, "unknown")]
