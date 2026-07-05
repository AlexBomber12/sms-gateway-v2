from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import (
    IncomingSms,
    MessageDeleteFailed,
    MessageReadMissing,
    MessageReadSkipped,
)
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
    modem_client.read_message.return_value = sample_sms
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
    modem_client.read_message.return_value = sample_sms

    await fire_added_signal(sample_sms.object_path)

    assert queue.enqueue.await_count == 1
    assert metric_value(metrics, "sms_dedup_hits_total") == 1.0
    assert metric_value(metrics, "sms_received_total") == 0.0
    modem_client.delete_message.assert_awaited_once_with(sample_sms.object_path)


async def test_on_new_sms_logs_and_skips_when_path_not_found(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    await register_relay_callback(queue, modem_client, relay)
    missing_path = "/org/freedesktop/ModemManager1/SMS/missing"
    modem_client.read_message.side_effect = MessageReadMissing("SMS vanished")
    queue.enqueue = AsyncMock(wraps=queue.enqueue)

    await fire_added_signal(missing_path)

    logger.warning.assert_not_called()
    logger.info.assert_called_once_with(
        "relay_sms_read_missing",
        sms_path=missing_path,
        error="SMS vanished",
    )
    assert relay._pending_text_retries == {}
    queue.enqueue.assert_not_awaited()
    modem_client.delete_message.assert_not_awaited()


async def test_on_new_sms_logs_and_skips_non_inbound_read(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    await register_relay_callback(queue, modem_client, relay)
    skipped_path = "/org/freedesktop/ModemManager1/SMS/status-report"
    modem_client.read_message.side_effect = MessageReadSkipped("SMS object is not inbound")
    queue.enqueue = AsyncMock(wraps=queue.enqueue)

    await fire_added_signal(skipped_path)

    logger.info.assert_called_once_with(
        "relay_sms_read_skipped",
        sms_path=skipped_path,
        error="SMS object is not inbound",
    )
    assert relay._pending_text_retries == {}
    queue.enqueue.assert_not_awaited()
    modem_client.delete_message.assert_not_awaited()


async def test_relay_schedules_retry_when_read_message_returns_none(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    await register_relay_callback(queue, modem_client, relay)
    sms_path = "/org/freedesktop/ModemManager1/SMS/undecoded"
    modem_client.read_message.return_value = None

    await fire_added_signal(sms_path)

    assert sms_path in relay._pending_text_retries
    assert not relay._pending_text_retries[sms_path].done()
    modem_client.read_message.assert_awaited_once_with(sms_path)
    modem_client.delete_message.assert_not_awaited()
    await relay._cancel_pending_text_retries()


async def test_relay_retry_recovers_when_text_becomes_available(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")

    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    queue.enqueue = AsyncMock(wraps=queue.enqueue)
    modem_client.read_message.side_effect = [None, sample_sms]

    await fire_added_signal(sample_sms.object_path)
    await wait_until(lambda: modem_client.delete_message.await_count == 1)
    await wait_until(lambda: not relay._pending_text_retries)

    queue.enqueue.assert_awaited_once_with(sample_sms)
    modem_client.delete_message.assert_awaited_once_with(sample_sms.object_path)
    logger.info.assert_any_call(
        "sms_text_undecoded_retry_recovered",
        sms_path=sample_sms.object_path,
        attempts_used=1,
        total_wait_seconds=ANY,
    )


async def test_relay_retry_exhaustion_marks_undecodable_and_deletes(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    sms_path = "/org/freedesktop/ModemManager1/SMS/undecoded"
    modem_client.read_message.return_value = None

    await fire_added_signal(sms_path)
    await wait_until(lambda: modem_client.delete_message.await_count == 1)
    await wait_until(lambda: not relay._pending_text_retries)

    assert modem_client.read_message.await_count == 4
    assert metric_value(metrics, "sms_text_undecoded_total") == 1.0
    modem_client.delete_message.assert_awaited_once_with(sms_path)
    logger.error.assert_called_once_with(
        "sms_text_undecodable",
        sms_path=sms_path,
        total_wait_seconds=ANY,
        attempts_used=3,
    )


async def test_relay_retry_exhaustion_logs_delete_failure(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    modem_client.read_message.return_value = None
    modem_client.delete_message.side_effect = MessageDeleteFailed("delete failed")
    sms_path = "/org/freedesktop/ModemManager1/SMS/undecoded"

    await fire_added_signal(sms_path)
    await wait_until(lambda: modem_client.delete_message.await_count == 1)

    logger.warning.assert_called_once_with(
        "relay_sms_delete_failed",
        sms_path=sms_path,
        error="delete failed",
        delete_failures_count=1,
    )


async def test_relay_retry_stops_when_sms_path_disappears(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    sms_path = "/org/freedesktop/ModemManager1/SMS/vanished"
    modem_client.read_message.side_effect = [None, MessageReadMissing("SMS vanished")]

    await fire_added_signal(sms_path)
    await wait_until(lambda: not relay._pending_text_retries)

    assert modem_client.read_message.await_count == 2
    assert metric_value(metrics, "sms_text_undecoded_total") == 0.0
    modem_client.delete_message.assert_not_awaited()
    logger.info.assert_any_call(
        "sms_text_undecoded_retry_missing",
        sms_path=sms_path,
        attempts_used=1,
        total_wait_seconds=ANY,
        error="SMS vanished",
    )


async def test_relay_retry_stops_when_sms_path_becomes_non_inbound(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    sms_path = "/org/freedesktop/ModemManager1/SMS/status-report"
    modem_client.read_message.side_effect = [
        None,
        MessageReadSkipped("SMS object is not inbound"),
    ]

    await fire_added_signal(sms_path)
    await wait_until(lambda: not relay._pending_text_retries)

    assert modem_client.read_message.await_count == 2
    assert metric_value(metrics, "sms_text_undecoded_total") == 0.0
    modem_client.delete_message.assert_not_awaited()
    logger.info.assert_any_call(
        "sms_text_undecoded_retry_skipped",
        sms_path=sms_path,
        attempts_used=1,
        total_wait_seconds=ANY,
        error="SMS object is not inbound",
    )


def test_relay_retry_done_records_unexpected_task_error(
    relay: SmsRelay,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    task = MagicMock(spec=asyncio.Future)
    task.cancelled.return_value = False
    task.result.side_effect = RuntimeError("boom")
    relay._pending_text_retries["sms-path"] = task

    relay._handle_text_retry_done("sms-path", task)

    assert relay._pending_text_retries == {}
    assert relay.state().last_error == "boom"
    logger.exception.assert_called_once_with(
        "sms_text_undecoded_retry_failed",
        sms_path="sms-path",
        error="boom",
    )


async def test_relay_retry_state_cleaned_on_completion(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    await register_relay_callback(queue, modem_client, relay)
    modem_client.read_message.return_value = None

    await fire_added_signal("/org/freedesktop/ModemManager1/SMS/undecoded")
    await wait_until(lambda: metric_value(metrics, "sms_text_undecoded_total") == 1.0)
    await wait_until(lambda: not relay._pending_text_retries)

    assert relay._pending_text_retries == {}


async def test_relay_shutdown_cancels_pending_retries(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    fire_added_signal: FireAddedSignal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    await register_relay_callback(queue, modem_client, relay)
    modem_client.read_message.return_value = None
    sms_path = "/org/freedesktop/ModemManager1/SMS/undecoded"

    await fire_added_signal(sms_path)
    task = relay._pending_text_retries[sms_path]
    relay._status = "running"
    await relay.stop()

    assert task.cancelled()
    assert relay._pending_text_retries == {}


async def test_relay_deduplicates_concurrent_retries_for_same_sms_path(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    await queue.initialize()
    modem_client.read_message.return_value = None
    sms_path = "/org/freedesktop/ModemManager1/SMS/undecoded"

    await asyncio.gather(relay._on_new_sms(sms_path), relay._on_new_sms(sms_path))

    assert list(relay._pending_text_retries) == [sms_path]
    assert not relay._pending_text_retries[sms_path].done()
    await relay._cancel_pending_text_retries()


async def test_delete_failure_increments_metric_on_signal_path(
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
    modem_client.read_message.return_value = sample_sms
    modem_client.delete_message.side_effect = MessageDeleteFailed("delete failed")
    before_delete_failure = datetime.now(UTC)

    await fire_added_signal(sample_sms.object_path)

    logger.warning.assert_called_once_with(
        "relay_sms_delete_failed",
        sms_path=sample_sms.object_path,
        error="delete failed",
        delete_failures_count=1,
    )
    assert metric_value(metrics, "sms_received_total") == 1.0
    assert metric_value(metrics, "sms_delete_failures_total") == 1.0
    state = relay.state()
    assert state.sms_delete_failures_count == 1
    assert state.last_delete_failure_at is not None
    assert state.last_delete_failure_at >= before_delete_failure


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
    modem_client.read_message.return_value = sample_sms
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
    read_calls = 0

    async def read_message(sms_path: str) -> IncomingSms:
        nonlocal read_calls
        assert sms_path in {first.object_path, second.object_path}
        read_calls += 1
        if read_calls == 1:
            first_entered.set()
            await release_first.wait()
            return first
        return second

    modem_client.read_message = AsyncMock(side_effect=read_message)

    first_task = asyncio.create_task(relay._on_new_sms(first.object_path))
    await asyncio.wait_for(first_entered.wait(), timeout=1.0)
    second_task = asyncio.create_task(relay._on_new_sms(second.object_path))
    await asyncio.sleep(0)

    assert read_calls == 1
    assert not second_task.done()

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert read_calls == 2
    assert modem_client.delete_message.await_count == 2
