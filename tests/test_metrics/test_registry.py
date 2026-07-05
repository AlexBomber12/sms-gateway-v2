from __future__ import annotations

from prometheus_client import CollectorRegistry

from sms_gateway_v2.metrics import (
    MetricsRegistry,
    last_sms_received_seconds,
    last_telegram_success_seconds,
    modem_resets_total,
    modem_signal_percent,
    modem_state,
    queue_failed_count,
    queue_pending_count,
    queue_processing_count,
    queue_sent_count,
    sms_dedup_hits_total,
    sms_delivered_total,
    sms_failed_total,
    sms_received_total,
    telegram_send_duration_seconds,
    telegram_send_failures_total,
    telegram_send_total,
)
from sms_gateway_v2.metrics.registry import sms_text_undecoded_total

METRIC_NAMES = [
    sms_received_total,
    sms_delivered_total,
    sms_failed_total,
    sms_dedup_hits_total,
    sms_text_undecoded_total,
    queue_pending_count,
    queue_processing_count,
    queue_sent_count,
    queue_failed_count,
    last_sms_received_seconds,
    last_telegram_success_seconds,
    modem_signal_percent,
    modem_state,
    modem_resets_total,
    telegram_send_duration_seconds,
    telegram_send_total,
    telegram_send_failures_total,
]


def test_metrics_registry_creates_all_metrics_on_fresh_collector_registry() -> None:
    registry = MetricsRegistry()

    assert isinstance(registry.registry, CollectorRegistry)
    assert len(METRIC_NAMES) == 17
    for metric_name in METRIC_NAMES:
        assert hasattr(registry, metric_name)


def test_render_returns_bytes_containing_metric_names() -> None:
    output = MetricsRegistry().render()

    assert isinstance(output, bytes)
    for metric_name in METRIC_NAMES:
        assert metric_name.encode() in output


def test_two_metrics_registries_do_not_share_state() -> None:
    first = MetricsRegistry()
    second = MetricsRegistry()

    first.sms_received_total.inc()

    assert first.registry is not second.registry
    assert b"sms_received_total 1.0" in first.render()
    assert b"sms_received_total 1.0" not in second.render()
    assert b"sms_received_total 0.0" in second.render()


def test_modem_state_label_is_rendered() -> None:
    registry = MetricsRegistry()

    registry.modem_state.labels(state="registered").set(1)

    assert b'modem_state{state="registered"} 1.0' in registry.render()


def test_sms_text_undecoded_metric_renders_incremented_value() -> None:
    registry = MetricsRegistry()

    registry.sms_text_undecoded_total.inc()

    assert b"sms_text_undecoded_total 1.0" in registry.render()
