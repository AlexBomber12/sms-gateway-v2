from __future__ import annotations

from unittest.mock import MagicMock

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_worker.helpers import metric_value


async def test_unexpected_send_error_moves_item_to_failed_and_logs(
    monkeypatch,
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    telegram_client.send_message.side_effect = RuntimeError("boom")
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.worker.worker.logger", logger)

    assert await worker._process_one_pending_item() is True

    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert metric_value(metrics, "sms_failed_total") == 1.0
    assert metric_value(metrics, "telegram_send_total", {"result": "failure"}) == 1.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "exhausted"}) == 1.0
    logger.exception.assert_called_once_with(
        "delivery_unexpected_error",
        item_id=item.id,
        attempt=0,
        attempts_used=1,
        error="boom",
    )
