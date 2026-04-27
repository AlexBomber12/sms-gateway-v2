from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramMessage
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_worker.helpers import metric_value


async def test_run_loop_processes_single_enqueued_item(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    queue.mark_sent = AsyncMock(wraps=queue.mark_sent)

    task = asyncio.create_task(worker.run())
    try:
        worker.wakeup()
        await wait_until(lambda: (queue._dirs["sent"] / f"{item.id}.json").exists())
    finally:
        worker.stop()
        worker.wakeup()
        await asyncio.wait_for(task, timeout=1.0)

    queue.mark_sent.assert_awaited_once()
    assert metric_value(metrics, "sms_delivered_total") == 1.0
    assert metric_value(metrics, "last_telegram_success_seconds") > 0.0
    assert metric_value(metrics, "telegram_send_total", {"result": "success"}) == 1.0
    telegram_client.send_message.assert_awaited_once_with(
        TelegramMessage.from_sms(chat_id="-100", number=sample_sms.number, text=sample_sms.text)
    )


async def test_process_one_pending_item_sends_telegram_message_from_sms(
    queue: Queue,
    telegram_client: MagicMock,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None

    assert await worker._process_one_pending_item() is True

    telegram_client.send_message.assert_awaited_once_with(
        TelegramMessage.from_sms(chat_id="-100", number=item.sms.number, text=item.sms.text)
    )
