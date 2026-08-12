from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import structlog

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import ModemError, ModemManagerClient
from sms_gateway_v2.modem.models import RegistrationState

logger = structlog.get_logger(__name__)

ModemHealthCallback = Callable[
    [str, int | None, str | None, str],
    None,
]

_BAD_REGISTRATION_STATES = frozenset(
    {
        RegistrationState.DENIED,
        RegistrationState.SEARCHING,
        RegistrationState.UNKNOWN,
    }
)
_ENABLE_RECOVERABLE_MODEM_STATES = frozenset({"disabled", "locked", "failed"})
_INVALID_TRANSITION_FRAGMENT = "Invalid transition"


class ModemWatchdog:
    def __init__(
        self,
        modem_client: ModemManagerClient,
        metrics: MetricsRegistry,
        interval_seconds: float,
        signal_zero_threshold: int,
        bad_state_minutes: int,
        modem_health_callback: ModemHealthCallback | None = None,
        enable_cooldown_seconds: float = 120.0,
        enable_frozen_cooldown_seconds: float = 1800.0,
    ) -> None:
        self._modem_client = modem_client
        self._metrics = metrics
        self._interval_seconds = interval_seconds
        self._signal_zero_threshold = signal_zero_threshold
        self._bad_state_minutes = bad_state_minutes
        self._modem_health_callback = modem_health_callback
        self._enable_cooldown_seconds = enable_cooldown_seconds
        self._enable_frozen_cooldown_seconds = enable_frozen_cooldown_seconds
        self._last_enable_attempt_at: datetime | None = None
        self._consecutive_zero_signal_polls: int = 0
        self._bad_state_since: datetime | None = None
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self._poll_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )

    async def _poll_once(self) -> None:
        try:
            signal = await self._modem_client.get_signal_quality()
            state = await self._modem_client.get_registration_state()
            modem_state = await self._modem_client.get_modem_state()
        except ModemError as exc:
            logger.warning("watchdog_poll_failed", error=str(exc))
            self._mark_modem_health_unavailable()
            return
        operator = await self._read_operator_name()
        signal_percent = signal.percent

        if self._modem_health_callback is not None:
            self._modem_health_callback(
                modem_state,
                signal_percent,
                operator,
                state.value,
            )

        self._metrics.modem_signal_percent.set(signal_percent)
        for known_state in RegistrationState:
            value = 1 if known_state == state else 0
            self._metrics.modem_state.labels(state=known_state.value).set(value)

        if not signal.recent:
            logger.info(
                "watchdog_signal_stale_skipped",
                percent=signal.percent,
                consecutive_zero_signal_polls=self._consecutive_zero_signal_polls,
            )
        elif signal.percent == 0:
            self._consecutive_zero_signal_polls += 1
        else:
            self._consecutive_zero_signal_polls = 0

        now = datetime.now(UTC)
        if state in _BAD_REGISTRATION_STATES:
            if self._bad_state_since is None:
                self._bad_state_since = now
        else:
            self._bad_state_since = None

        zero_signal_triggered = self._consecutive_zero_signal_polls >= self._signal_zero_threshold
        bad_state_triggered = self._bad_state_since is not None and (
            now - self._bad_state_since >= timedelta(minutes=self._bad_state_minutes)
        )
        if zero_signal_triggered or bad_state_triggered:
            reason = "zero_signal" if zero_signal_triggered else "bad_state"
            if modem_state in _ENABLE_RECOVERABLE_MODEM_STATES:
                await self._try_enable(reason, modem_state)
            else:
                await self._try_reset(reason, state)
            self._consecutive_zero_signal_polls = 0
            self._bad_state_since = None

    async def _try_reset(self, reason: str, state: RegistrationState) -> None:
        logger.warning(
            "watchdog_triggering_modem_reset",
            reason=reason,
            consecutive_zero_signal_polls=self._consecutive_zero_signal_polls,
            state=state.value,
        )
        try:
            await self._modem_client.reset()
        except ModemError as exc:
            logger.warning("watchdog_reset_failed", reason=reason, error=str(exc))
        else:
            self._metrics.modem_resets_total.inc()

    async def _try_enable(self, reason: str, modem_state: str) -> None:
        now = datetime.now(UTC)
        seconds_remaining = self._enable_cooldown_seconds_remaining(now)
        if seconds_remaining > 0:
            logger.info(
                "watchdog_enable_cooldown_skipped",
                modem_state=modem_state,
                seconds_remaining=seconds_remaining,
            )
            return

        logger.warning(
            "watchdog_triggering_modem_enable",
            reason=reason,
            modem_state=modem_state,
            consecutive_zero_signal_polls=self._consecutive_zero_signal_polls,
        )
        self._last_enable_attempt_at = now
        try:
            await self._modem_client.enable()
        except ModemError as exc:
            error = str(exc)
            if self._is_invalid_transition_error(error):
                logger.error(
                    "watchdog_modem_frozen",
                    modem_state=modem_state,
                    error=error,
                )
                self._defer_next_enable_attempt(now, self._enable_frozen_cooldown_seconds)
                return
            logger.warning(
                "watchdog_enable_failed",
                reason=reason,
                modem_state=modem_state,
                error=error,
            )
            return

        self._metrics.modem_enables_total.inc()

    def _enable_cooldown_seconds_remaining(self, now: datetime) -> float:
        if self._last_enable_attempt_at is None:
            return 0.0
        next_attempt_at = self._last_enable_attempt_at + timedelta(
            seconds=self._enable_cooldown_seconds
        )
        return (next_attempt_at - now).total_seconds()

    def _defer_next_enable_attempt(self, now: datetime, cooldown_seconds: float) -> None:
        self._last_enable_attempt_at = now + timedelta(
            seconds=cooldown_seconds - self._enable_cooldown_seconds
        )

    @staticmethod
    def _is_invalid_transition_error(error: str) -> bool:
        # ModemManager reaches Python as a generic ModemError wrapper here, so
        # the D-Bus error text is the only stable signal for this firmware freeze.
        return _INVALID_TRANSITION_FRAGMENT in error

    async def _read_operator_name(self) -> str | None:
        try:
            return await self._modem_client.get_operator_name()
        except ModemError as exc:
            logger.warning("watchdog_operator_name_read_failed", error=str(exc))
            return None

    def _mark_modem_health_unavailable(self) -> None:
        if self._modem_health_callback is not None:
            self._modem_health_callback("unavailable", None, None, "unknown")

    def stop(self) -> None:
        self._stop_event.set()
