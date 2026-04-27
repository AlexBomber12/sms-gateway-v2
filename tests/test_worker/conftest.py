from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.worker import DeliveryWorker


@pytest.fixture
async def queue(tmp_path: Path) -> AsyncIterator[Queue]:
    queue = Queue(tmp_path / "state", dedup_window_minutes=1)
    await queue.initialize()
    try:
        yield queue
    finally:
        await queue.close()


@pytest.fixture
def telegram_client() -> MagicMock:
    client = MagicMock(spec=TelegramClient)
    client.send_message = AsyncMock()
    return client


@pytest.fixture
def metrics() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture
def worker(
    queue: Queue,
    telegram_client: MagicMock,
    metrics: MetricsRegistry,
) -> DeliveryWorker:
    return DeliveryWorker(
        queue=queue,
        telegram_client=telegram_client,
        telegram_chat_id="-100",
        metrics=metrics,
        retry_schedule_seconds=(1, 2, 4),
    )


@pytest.fixture
def sample_sms() -> IncomingSms:
    return IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="+15551234567",
        text="hello",
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )


@pytest.fixture
def wait_until() -> Callable[[Callable[[], bool]], Awaitable[None]]:
    async def _wait_until(predicate: Callable[[], bool]) -> None:
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition was not met before timeout")

    return _wait_until
