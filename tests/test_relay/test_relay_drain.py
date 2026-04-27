from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.relay import SmsRelay
from tests.test_relay.conftest import SmsFactory
from tests.test_worker.helpers import metric_value


async def test_start_drains_existing_messages_from_modem(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    sms_factory: SmsFactory,
) -> None:
    first = sms_factory(text="first")
    second = sms_factory(
        object_path="/org/freedesktop/ModemManager1/SMS/2",
        text="second",
    )
    modem_client.list_messages.return_value = [first, second]
    queue.enqueue = AsyncMock(wraps=queue.enqueue)

    await relay.start()
    try:
        assert queue.enqueue.await_count == 2
        modem_client.delete_message.assert_has_awaits(
            [call(first.object_path), call(second.object_path)]
        )
        assert metric_value(metrics, "sms_received_total") == 2.0
    finally:
        await relay.stop()


async def test_start_handles_empty_drain_gracefully(
    relay: SmsRelay,
    modem_client: MagicMock,
    metrics: MetricsRegistry,
) -> None:
    await relay.start()
    try:
        assert modem_client.list_messages.await_count == 1
        modem_client.delete_message.assert_not_awaited()
        assert metric_value(metrics, "sms_received_total") == 0.0
    finally:
        await relay.stop()
