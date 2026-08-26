from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from html import escape

import structlog
from pydantic import ValidationError

from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.relay.relay import SmsRelay
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.telegram.exceptions import TelegramError
from sms_gateway_v2.telegram.models import TelegramMessage
from sms_gateway_v2.util import format_duration_since

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _FormattedHeartbeat:
    text: str
    reasons: tuple[str, ...]


class HeartbeatScheduler:
    def __init__(
        self,
        telegram_client: TelegramClient,
        relay: SmsRelay,
        chat_id: str,
        interval_seconds: float,
    ) -> None:
        self._telegram_client = telegram_client
        self._relay = relay
        self._chat_id = chat_id
        self._interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
            if self._stop_event.is_set():
                return
            await self._send_heartbeat()

    async def _send_heartbeat(self) -> None:
        state = self._relay.state()
        formatted = _format_heartbeat(state)
        degraded = bool(formatted.reasons)
        try:
            message = TelegramMessage(
                chat_id=self._chat_id,
                text=formatted.text,
                disable_notification=not degraded,
            )
            await self._telegram_client.send_message(message)
        except (TelegramError, ValidationError) as exc:
            logger.warning("heartbeat_send_failed", error=str(exc))
        else:
            logger.info("heartbeat_sent", chat_id=self._chat_id, degraded=degraded)

    def stop(self) -> None:
        self._stop_event.set()


def _format_heartbeat(state: RelayState) -> _FormattedHeartbeat:
    reasons = _degradation_reasons(state)
    return _FormattedHeartbeat(
        text=_format_heartbeat_text(state, reasons),
        reasons=tuple(reasons),
    )


def _format_heartbeat_text(state: RelayState, reasons: Sequence[str] | None = None) -> str:
    reason_lines = tuple(_degradation_reasons(state) if reasons is None else reasons)
    lines = [f"<b>{_format_title(state, reason_lines)}</b>"]

    queue_line = _format_queue_line(state)
    if queue_line is not None:
        lines.append(queue_line)
    if state.sms_delete_failures_count > 0:
        lines.append(f"Delete failures: {state.sms_delete_failures_count}")
    if state.last_error is not None:
        lines.append(f"Error: {escape(state.last_error)}")

    lines.extend([_format_modem_line(state), _format_ages_line(state)])
    return "\n".join(lines)


def _format_title(state: RelayState, reasons: Sequence[str]) -> str:
    if not reasons:
        return "✅ SMS Gateway"

    title = f"🔴 SMS Gateway: {_primary_title_fragment(state)}"
    extra_count = len(reasons) - 1
    if extra_count > 0:
        title = f"{title} +{extra_count}"
    return title


def _primary_title_fragment(state: RelayState) -> str:
    if state.modem_state is not None and state.modem_state != "registered":
        return f"modem {escape(state.modem_state)}"
    return f"{state.sms_delete_failures_count} delete failures"


def _degradation_reasons(state: RelayState) -> list[str]:
    reasons: list[str] = []
    if state.modem_state is not None and state.modem_state != "registered":
        reasons.append(f"- modem state: {escape(state.modem_state)} (expected registered)")
    if state.sms_delete_failures_count > 0:
        reasons.append(f"- delete failures: {state.sms_delete_failures_count}")
    return reasons


def _format_modem_line(state: RelayState) -> str:
    if state.modem_state is None:
        return "Modem: unknown"
    line = f"Modem: {escape(state.modem_state)}"
    if state.modem_signal_percent is not None:
        line = f"{line} · {state.modem_signal_percent}%"
    if state.modem_operator:
        line = f"{line} · {escape(state.modem_operator)}"
    if state.modem_registration and state.modem_registration != "unknown":
        line = f"{line} ({escape(state.modem_registration)})"
    return line


def _format_queue_line(state: RelayState) -> str | None:
    pending_visible = state.queue_pending_count is not None and state.queue_pending_count > 0
    failed_visible = state.queue_failed_count is not None and state.queue_failed_count > 0
    if not pending_visible and not failed_visible:
        return None
    return (
        f"Queue: {_format_queue_count(state.queue_pending_count)} pending, "
        f"{_format_queue_count(state.queue_failed_count)} failed"
    )


def _format_queue_count(value: int | None) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _format_ages_line(state: RelayState) -> str:
    sms_age = (
        "SMS never"
        if state.last_sms_received_at is None
        else f"SMS {format_duration_since(state.last_sms_received_at)} ago"
    )
    uptime = (
        "uptime unknown"
        if state.started_at is None
        else f"uptime {format_duration_since(state.started_at)}"
    )
    return f"{sms_age} · {uptime}"
