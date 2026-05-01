from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime

import structlog

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
        text = (
            "<b>SMS Gateway v2: alive</b>\n"
            f"Status: {state.status}\n"
            f"Started: {_format_timestamp(state.started_at)}\n"
            f"Last SMS: {_format_timestamp(state.last_sms_received_at)}\n"
            f"Last error: {state.last_error if state.last_error else '(none)'}"
        )
        message = TelegramMessage(chat_id=self._chat_id, text=text)
        try:
            await self._telegram_client.send_message(message)
        except TelegramError as exc:
            logger.warning("heartbeat_send_failed", error=str(exc))
        else:
            logger.info("heartbeat_sent", chat_id=self._chat_id)

    def stop(self) -> None:
        self._stop_event.set()


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "(none)"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")
