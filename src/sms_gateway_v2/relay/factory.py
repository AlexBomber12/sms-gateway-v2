from __future__ import annotations

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry, QueueGaugeUpdater
from sms_gateway_v2.modem import ModemManagerClient
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.worker import DeliveryWorker

from .cleanup_scheduler import CleanupScheduler
from .exceptions import RelayError
from .heartbeat import HeartbeatScheduler
from .relay import SmsRelay
from .watchdog import ModemWatchdog


def build_relay(
    settings: Settings,
    metrics: MetricsRegistry,
) -> tuple[
    SmsRelay,
    QueueGaugeUpdater,
    ModemWatchdog,
    CleanupScheduler,
    HeartbeatScheduler | None,
]:
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
    relay = SmsRelay(
        modem_client=modem_client,
        queue=queue,
        telegram_client=telegram_client,
        worker=worker,
        metrics=metrics,
    )
    gauge_updater = QueueGaugeUpdater(
        queue=queue,
        metrics=metrics,
        interval_seconds=settings.queue_gauge_interval_seconds,
    )
    watchdog = ModemWatchdog(
        modem_client=modem_client,
        metrics=metrics,
        interval_seconds=settings.modem_watchdog_interval_seconds,
        signal_zero_threshold=settings.modem_watchdog_signal_zero_threshold,
        bad_state_minutes=settings.modem_watchdog_bad_state_minutes,
    )
    cleanup_scheduler = CleanupScheduler(
        queue=queue,
        sent_retention_days=settings.queue_sent_retention_days,
        failed_retention_days=settings.queue_failed_retention_days,
        interval_seconds=settings.cleanup_interval_seconds,
    )
    heartbeat_scheduler: HeartbeatScheduler | None = None
    if settings.heartbeat_enabled:
        heartbeat_chat_id = (
            settings.heartbeat_telegram_chat_id
            if settings.heartbeat_telegram_chat_id != ""
            else settings.telegram_chat_id
        )
        heartbeat_scheduler = HeartbeatScheduler(
            telegram_client=telegram_client,
            relay=relay,
            chat_id=heartbeat_chat_id,
            interval_seconds=settings.heartbeat_interval_seconds,
        )
    return relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler
