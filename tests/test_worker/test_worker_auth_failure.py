from __future__ import annotations

from unittest.mock import MagicMock

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramAuthError
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_worker.helpers import metric_value


async def test_auth_error_moves_item_to_failed_without_retry(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    telegram_client.send_message.side_effect = TelegramAuthError("unauthorized")

    assert await worker._process_one_pending_item() is True

    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert not (queue._dirs["pending"] / f"{item.id}.json").exists()
    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert metric_value(metrics, "sms_failed_total") == 1.0
    assert metric_value(metrics, "telegram_send_total", {"result": "failure"}) == 1.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "auth_error"}) == 1.0
