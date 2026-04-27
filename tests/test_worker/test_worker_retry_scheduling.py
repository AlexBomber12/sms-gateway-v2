from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue, QueueItem
from sms_gateway_v2.queue.paths import load_item, save_item
from sms_gateway_v2.telegram import TelegramError, TelegramRateLimited, TelegramTransportError
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_worker.helpers import metric_value


async def test_transport_error_schedules_retry(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    telegram_client.send_message.side_effect = TelegramTransportError("network")
    before = datetime.now(UTC) + timedelta(seconds=1)

    assert await worker._process_one_pending_item() is True

    after = datetime.now(UTC) + timedelta(seconds=1)
    pending_item = load_item(queue._dirs["pending"] / f"{item.id}.json")
    assert pending_item.attempts == 1
    assert pending_item.next_retry_at is not None
    assert before <= pending_item.next_retry_at <= after
    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert metric_value(metrics, "sms_failed_total") == 0.0
    assert metric_value(metrics, "telegram_send_total", {"result": "failure"}) == 1.0
    assert (
        metric_value(metrics, "telegram_send_failures_total", {"reason": "transport_error"}) == 1.0
    )


async def test_rate_limit_schedules_retry(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    telegram_client.send_message.side_effect = TelegramRateLimited("too many", retry_after=2.0)

    assert await worker._process_one_pending_item() is True

    pending_item = load_item(queue._dirs["pending"] / f"{item.id}.json")
    assert pending_item.attempts == 1
    assert pending_item.next_retry_at is not None
    assert metric_value(metrics, "sms_failed_total") == 0.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "rate_limited"}) == 1.0


async def test_generic_telegram_error_schedules_retry_with_exhausted_reason(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    telegram_client.send_message.side_effect = TelegramError("telegram rejected request")

    assert await worker._process_one_pending_item() is True

    pending_item = load_item(queue._dirs["pending"] / f"{item.id}.json")
    assert pending_item.attempts == 1
    assert metric_value(metrics, "sms_failed_total") == 0.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "exhausted"}) == 1.0


async def test_transport_error_exhausts_retry_budget(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    item = await _save_pending_attempts(queue, item, attempts=2)
    telegram_client.send_message.side_effect = TelegramTransportError("network")

    assert await worker._process_one_pending_item() is True

    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert metric_value(metrics, "sms_failed_total") == 1.0
    assert metric_value(metrics, "telegram_send_total", {"result": "failure"}) == 1.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "exhausted"}) == 1.0


async def test_not_yet_due_retry_moves_back_to_pending_without_work(
    queue: Queue,
    telegram_client: MagicMock,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    item = item.model_copy(update={"next_retry_at": datetime.now(UTC) + timedelta(minutes=5)})
    await asyncio.to_thread(save_item, item, queue._dirs["pending"])

    assert await worker._process_one_pending_item() is False

    assert (queue._dirs["pending"] / f"{item.id}.json").exists()
    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    telegram_client.send_message.assert_not_awaited()


async def _save_pending_attempts(queue: Queue, item: QueueItem, *, attempts: int) -> QueueItem:
    updated = item.model_copy(update={"attempts": attempts})
    await asyncio.to_thread(save_item, updated, queue._dirs["pending"])
    return updated
