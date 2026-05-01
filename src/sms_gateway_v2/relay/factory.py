from __future__ import annotations

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import ModemManagerClient
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.worker import DeliveryWorker

from .exceptions import RelayError
from .relay import SmsRelay


def build_relay(settings: Settings, metrics: MetricsRegistry) -> SmsRelay:
    if settings.telegram_bot_token == "":
        raise RelayError("telegram_bot_token must not be empty when relay_enabled is true")
    if settings.telegram_chat_id == "":
        raise RelayError("telegram_chat_id must not be empty when relay_enabled is true")

    modem_client = ModemManagerClient()
    queue = Queue(
        state_dir=settings.state_dir,
        dedup_window_minutes=settings.dedup_window_minutes,
    )
    telegram_client = TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.telegram_timeout_seconds,
        max_retries=settings.telegram_max_retries,
    )
    worker = DeliveryWorker(
        queue=queue,
        telegram_client=telegram_client,
        telegram_chat_id=settings.telegram_chat_id,
        metrics=metrics,
        retry_schedule_seconds=settings.worker_retry_schedule_seconds,
    )
    return SmsRelay(
        modem_client=modem_client,
        queue=queue,
        telegram_client=telegram_client,
        worker=worker,
        metrics=metrics,
    )
