from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from html import escape

import structlog
from pydantic import ValidationError

from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.relay.relay import SmsRelay
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.telegram.exceptions import TelegramError
from sms_gateway_v2.telegram.models import TelegramMessage

logger = structlog.get_logger(__name__)


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
        text = _format_heartbeat_text(state)
        try:
            message = TelegramMessage(chat_id=self._chat_id, text=text)
            await self._telegram_client.send_message(message)
        except (TelegramError, ValidationError) as exc:
            logger.warning("heartbeat_send_failed", error=str(exc))
        else:
            logger.info("heartbeat_sent", chat_id=self._chat_id)

    def stop(self) -> None:
        self._stop_event.set()


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "(none)"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_heartbeat_text(state: RelayState) -> str:
    title = "SMS Gateway v2: alive"
    reasons = _degradation_reasons(state)
    if reasons:
        title = "SMS Gateway v2: ALERT"

    lines = [f"<b>{title}</b>"]
    if reasons:
        lines.extend(["<b>ALERT</b>", "<b>Reasons:</b>", *reasons, ""])

    last_error = escape(state.last_error) if state.last_error else "(none)"
    lines.extend(
        [
            f"Status: {escape(state.status)}",
            f"Started: {_format_timestamp(state.started_at)}",
            f"Last SMS: {_format_timestamp(state.last_sms_received_at)}",
            f"Last error: {last_error}",
            "",
            _format_modem_line(state),
            _format_queue_line(state),
            f"Deletes failed: {state.sms_delete_failures_count}",
            f"Last Telegram OK: {_format_timestamp(state.last_telegram_success_at)}",
        ]
    )
    return "\n".join(lines)


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
    signal = (
        f"{state.modem_signal_percent}%" if state.modem_signal_percent is not None else "unknown"
    )
    operator = escape(state.modem_operator) if state.modem_operator else "unknown"
    registration = escape(state.modem_registration) if state.modem_registration else "unknown"
    return f"Modem: {escape(state.modem_state)}, signal {signal}, {operator} ({registration})"


def _format_queue_line(state: RelayState) -> str:
    pending = state.queue_pending_count if state.queue_pending_count is not None else "unknown"
    failed = state.queue_failed_count if state.queue_failed_count is not None else "unknown"
    return f"Queue: pending {pending}, failed {failed}"
