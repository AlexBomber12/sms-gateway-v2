from __future__ import annotations

from sms_gateway_v2.metrics import MetricsRegistry


def metric_value(
    metrics: MetricsRegistry,
    name: str,
    labels: dict[str, str] | None = None,
) -> float:
    value = metrics.registry.get_sample_value(name, labels)
    if value is None:
        return 0.0
    return value
