from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

sms_received_total = "sms_received_total"
sms_delivered_total = "sms_delivered_total"
sms_failed_total = "sms_failed_total"
sms_dedup_hits_total = "sms_dedup_hits_total"
sms_text_undecoded_total = "sms_text_undecoded_total"
sms_delete_failures_total = "sms_delete_failures_total"
queue_pending_count = "queue_pending_count"
queue_processing_count = "queue_processing_count"
queue_sent_count = "queue_sent_count"
queue_failed_count = "queue_failed_count"
last_sms_received_seconds = "last_sms_received_seconds"
last_telegram_success_seconds = "last_telegram_success_seconds"
modem_signal_percent = "modem_signal_percent"
modem_state = "modem_state"
modem_resets_total = "modem_resets_total"
telegram_send_duration_seconds = "telegram_send_duration_seconds"
telegram_send_total = "telegram_send_total"
telegram_send_failures_total = "telegram_send_failures_total"


class MetricsRegistry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.sms_received_total = Counter(
            sms_received_total,
            "Total SMS messages received from modem and enqueued.",
            registry=self.registry,
        )
        self.sms_delivered_total = Counter(
            sms_delivered_total,
            "Total SMS messages successfully delivered to Telegram.",
            registry=self.registry,
        )
        self.sms_failed_total = Counter(
            sms_failed_total,
            "Total SMS messages that exhausted retries and were marked failed.",
            registry=self.registry,
        )
        self.sms_dedup_hits_total = Counter(
            sms_dedup_hits_total,
            "Total enqueue attempts skipped because of dedup match.",
            registry=self.registry,
        )
        self.sms_text_undecoded_total = Counter(
            sms_text_undecoded_total,
            "Total SMS dropped because Text remained empty across all decode retries.",
            registry=self.registry,
        )
        self.sms_delete_failures_total = Counter(
            sms_delete_failures_total,
            "Total SMS delete failures from the modem storage after successful Telegram delivery.",
            registry=self.registry,
        )
        self.queue_pending_count = Gauge(
            queue_pending_count,
            "Current number of items in pending state.",
            registry=self.registry,
        )
        self.queue_processing_count = Gauge(
            queue_processing_count,
            "Current number of items in processing state.",
            registry=self.registry,
        )
        self.queue_sent_count = Gauge(
            queue_sent_count,
            "Current number of items in sent state, before cleanup.",
            registry=self.registry,
        )
        self.queue_failed_count = Gauge(
            queue_failed_count,
            "Current number of items in failed state, before cleanup.",
            registry=self.registry,
        )
        self.last_sms_received_seconds = Gauge(
            last_sms_received_seconds,
            "Unix epoch seconds of the last SMS received from modem.",
            registry=self.registry,
        )
        self.last_telegram_success_seconds = Gauge(
            last_telegram_success_seconds,
            "Unix epoch seconds of the last successful Telegram delivery.",
            registry=self.registry,
        )
        self.modem_signal_percent = Gauge(
            modem_signal_percent,
            "Last reported modem signal percent 0 to 100.",
            registry=self.registry,
        )
        self.modem_state = Gauge(
            modem_state,
            "Modem state as reported by ModemManager. "
            "Value is 1 for current state and 0 for others.",
            ["state"],
            registry=self.registry,
        )
        self.modem_resets_total = Counter(
            modem_resets_total,
            "Total modem reset operations performed by the watchdog.",
            registry=self.registry,
        )
        self.telegram_send_duration_seconds = Histogram(
            telegram_send_duration_seconds,
            "Telegram send_message HTTP call duration.",
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
            registry=self.registry,
        )
        self.telegram_send_total = Counter(
            telegram_send_total,
            "Total Telegram send_message calls. result is success or failure.",
            ["result"],
            registry=self.registry,
        )
        self.telegram_send_failures_total = Counter(
            telegram_send_failures_total,
            "Total Telegram send failures broken down by reason: "
            "rate_limited, auth_error, transport_error, exhausted.",
            ["reason"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
