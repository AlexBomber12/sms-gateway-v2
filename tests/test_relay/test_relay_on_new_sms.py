from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import IncomingSms, MessageDeleteFailed
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.relay import SmsRelay
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_relay.conftest import FireAddedSignal, SmsFactory
from tests.test_worker.helpers import metric_value


async def register_relay_callback(
    queue: Queue,
    modem_client: MagicMock,
    relay: SmsRelay,
) -> None:
    await queue.initialize()
    await modem_client.watch_added(relay._on_new_sms)


async def test_on_new_sms_enqueues_wakes_worker_deletes_and_updates_metrics(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    worker: DeliveryWorker,
    metrics: MetricsRegistry,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
) -> None:
    await register_relay_callback(queue, modem_client, relay)
    modem_client.list_messages.return_value = [sample_sms]
    queue.enqueue = AsyncMock(wraps=queue.enqueue)
    worker.wakeup = MagicMock(wraps=worker.wakeup)

    await fire_added_signal(sample_sms.object_path)

    queue.enqueue.assert_awaited_once_with(sample_sms)
    worker.wakeup.assert_called_once()
    modem_client.delete_message.assert_awaited_once_with(sample_sms.object_path)
    assert metric_value(metrics, "sms_received_total") == 1.0
    assert metric_value(metrics, "last_sms_received_seconds") > 0.0
    assert relay.state().last_sms_received_at is not None


async def test_on_new_sms_duplicate_increments_dedup_and_still_deletes(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
) -> None:
    await register_relay_callback(queue, modem_client, relay)
    assert await queue.enqueue(sample_sms) is not None
    queue.enqueue = AsyncMock(wraps=queue.enqueue)
    modem_client.list_messages.return_value = [sample_sms]

    await fire_added_signal(sample_sms.object_path)

    assert queue.enqueue.await_count == 1
    assert metric_value(metrics, "sms_dedup_hits_total") == 1.0
    assert metric_value(metrics, "sms_received_total") == 0.0
    modem_client.delete_message.assert_awaited_once_with(sample_sms.object_path)


async def test_on_new_sms_logs_and_skips_when_path_not_found(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sample_sms: IncomingSms,
    sms_factory: SmsFactory,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    await register_relay_callback(queue, modem_client, relay)
    missing_path = "/org/freedesktop/ModemManager1/SMS/missing"
    modem_client.list_messages.return_value = [
        sms_factory(object_path=sample_sms.object_path, text="other")
    ]
    queue.enqueue = AsyncMock(wraps=queue.enqueue)

    await fire_added_signal(missing_path)

    logger.warning.assert_called_once_with("relay_sms_path_not_found", sms_path=missing_path)
    queue.enqueue.assert_not_awaited()
    modem_client.delete_message.assert_not_awaited()


async def test_on_new_sms_logs_delete_failure_without_raising(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    await register_relay_callback(queue, modem_client, relay)
    modem_client.list_messages.return_value = [sample_sms]
    modem_client.delete_message.side_effect = MessageDeleteFailed("delete failed")

    await fire_added_signal(sample_sms.object_path)

    logger.warning.assert_called_once_with(
        "relay_sms_delete_failed",
        sms_path=sample_sms.object_path,
        error="delete failed",
    )
    assert metric_value(metrics, "sms_received_total") == 1.0


async def test_on_new_sms_records_unexpected_enqueue_error_without_raising(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    worker: DeliveryWorker,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    await register_relay_callback(queue, modem_client, relay)
    modem_client.list_messages.return_value = [sample_sms]
    queue.enqueue = AsyncMock(side_effect=RuntimeError("boom"))
    worker.wakeup = MagicMock(wraps=worker.wakeup)

    await fire_added_signal(sample_sms.object_path)

    logger.exception.assert_called_once_with(
        "relay_sms_handler_error",
        sms_path=sample_sms.object_path,
        error="boom",
    )
    assert relay.state().last_error == "boom"
    worker.wakeup.assert_not_called()
    modem_client.delete_message.assert_not_awaited()


async def test_concurrent_on_new_sms_calls_are_serialized(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sms_factory: SmsFactory,
) -> None:
    await queue.initialize()
    first = sms_factory(text="first")
    second = sms_factory(
        object_path="/org/freedesktop/ModemManager1/SMS/2",
        text="second",
    )
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    list_calls = 0

    async def list_messages() -> list[IncomingSms]:
        nonlocal list_calls
        list_calls += 1
        if list_calls == 1:
            first_entered.set()
            await release_first.wait()
            return [first]
        return [second]

    modem_client.list_messages = AsyncMock(side_effect=list_messages)

    first_task = asyncio.create_task(relay._on_new_sms(first.object_path))
    await asyncio.wait_for(first_entered.wait(), timeout=1.0)
    second_task = asyncio.create_task(relay._on_new_sms(second.object_path))
    await asyncio.sleep(0)

    assert list_calls == 1
    assert not second_task.done()

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert list_calls == 2
    assert modem_client.delete_message.await_count == 2
