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


class ModemWatchdog:
    def __init__(
        self,
        modem_client: ModemManagerClient,
        metrics: MetricsRegistry,
        interval_seconds: float,
        signal_zero_threshold: int,
        bad_state_minutes: int,
        modem_health_callback: ModemHealthCallback | None = None,
    ) -> None:
        self._modem_client = modem_client
        self._metrics = metrics
        self._interval_seconds = interval_seconds
        self._signal_zero_threshold = signal_zero_threshold
        self._bad_state_minutes = bad_state_minutes
        self._modem_health_callback = modem_health_callback
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
            info = await self._modem_client.get_modem_info()
        except ModemError as exc:
            logger.warning("watchdog_poll_failed", error=str(exc))
            return
        signal = info.signal
        state = info.registration
        signal_percent = signal.percent if signal is not None else None

        if self._modem_health_callback is not None:
            self._modem_health_callback(
                info.state,
                signal_percent,
                info.sim_operator_name,
                state.value,
            )

        if signal_percent is not None:
            self._metrics.modem_signal_percent.set(signal_percent)
        for known_state in RegistrationState:
            value = 1 if known_state == state else 0
            self._metrics.modem_state.labels(state=known_state.value).set(value)

        if signal is None:
            logger.info(
                "watchdog_signal_missing_skipped",
                consecutive_zero_signal_polls=self._consecutive_zero_signal_polls,
            )
        elif not signal.recent:
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
            self._consecutive_zero_signal_polls = 0
            self._bad_state_since = None

    def stop(self) -> None:
        self._stop_event.set()
