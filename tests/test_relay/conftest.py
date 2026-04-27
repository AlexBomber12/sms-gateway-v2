from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms, ModemManagerClient
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.relay import SmsRelay
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.worker import DeliveryWorker

SMS_PATH = "/org/freedesktop/ModemManager1/SMS/1"

AddedCallback = Callable[[str], Awaitable[None]]
FireAddedSignal = Callable[[str], Awaitable[None]]
SmsFactory = Callable[..., IncomingSms]


class AddedSignalProbe:
    def __init__(self) -> None:
        self.callback: AddedCallback | None = None

    async def watch_added(self, callback: AddedCallback) -> None:
        self.callback = callback

    async def fire(self, sms_path: str) -> None:
        if self.callback is None:
            raise AssertionError("watch_added callback was not registered")
        await self.callback(sms_path)


@pytest.fixture
def added_signal_probe() -> AddedSignalProbe:
    return AddedSignalProbe()


@pytest.fixture
def modem_client(added_signal_probe: AddedSignalProbe) -> MagicMock:
    client = MagicMock(spec=ModemManagerClient)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.watch_added = AsyncMock(side_effect=added_signal_probe.watch_added)
    client.list_messages = AsyncMock(return_value=[])
    client.delete_message = AsyncMock()
    return client


@pytest.fixture
def fire_added_signal(added_signal_probe: AddedSignalProbe) -> FireAddedSignal:
    return added_signal_probe.fire


@pytest.fixture
async def queue(tmp_path: Path) -> AsyncIterator[Queue]:
    queue = Queue(tmp_path / "state", dedup_window_minutes=1)
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
def relay(
    modem_client: MagicMock,
    queue: Queue,
    telegram_client: MagicMock,
    worker: DeliveryWorker,
    metrics: MetricsRegistry,
) -> SmsRelay:
    return SmsRelay(
        modem_client=modem_client,
        queue=queue,
        telegram_client=telegram_client,
        worker=worker,
        metrics=metrics,
    )


@pytest.fixture
def sms_factory() -> SmsFactory:
    def make_sms(
        *,
        object_path: str = SMS_PATH,
        number: str = "+15551234567",
        text: str = "hello",
        timestamp: datetime | None = datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type: str = "deliver",
    ) -> IncomingSms:
        return IncomingSms(
            object_path=object_path,
            number=number,
            text=text,
            timestamp=timestamp,
            pdu_type=pdu_type,
        )

    return make_sms


@pytest.fixture
def sample_sms(sms_factory: SmsFactory) -> IncomingSms:
    return sms_factory()
