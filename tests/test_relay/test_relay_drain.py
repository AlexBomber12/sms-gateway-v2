from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import MessageDeleteFailed
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
    modem_client.read_message.side_effect = [first, second]
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


async def test_start_rolls_back_when_drain_enqueue_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sms_factory: SmsFactory,
) -> None:
    sms = sms_factory()
    modem_client.list_messages.return_value = [sms]
    modem_client.read_message.return_value = sms
    queue.enqueue = AsyncMock(side_effect=RuntimeError("enqueue failed"))
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(RuntimeError, match="enqueue failed"):
        await relay.start()

    assert relay.state().status == "stopped"
    assert relay.state().last_error == "enqueue failed"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()
    modem_client.delete_message.assert_not_awaited()


async def test_start_rolls_back_when_drain_delete_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    sms_factory: SmsFactory,
) -> None:
    sms = sms_factory()
    modem_client.list_messages.return_value = [sms]
    modem_client.read_message.return_value = sms
    modem_client.delete_message.side_effect = MessageDeleteFailed("delete failed")
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(MessageDeleteFailed, match="delete failed"):
        await relay.start()

    assert relay.state().status == "stopped"
    assert relay.state().last_error == "delete failed"
    assert relay.state().sms_delete_failures_count == 1
    assert relay.state().last_delete_failure_at is not None
    assert metric_value(metrics, "sms_delete_failures_total") == 1.0
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()
    modem_client.delete_message.assert_awaited_once_with(sms.object_path)


async def test_start_rollback_cancels_pending_text_retries(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sms_factory: SmsFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    undecoded = sms_factory(
        object_path="/org/freedesktop/ModemManager1/SMS/undecoded",
        text="",
    )
    later = sms_factory(
        object_path="/org/freedesktop/ModemManager1/SMS/later",
        text="later",
    )
    modem_client.list_messages.return_value = [undecoded, later]
    modem_client.read_message.side_effect = [None, later]
    modem_client.delete_message.side_effect = MessageDeleteFailed("delete failed")
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(MessageDeleteFailed, match="delete failed"):
        await relay.start()

    assert relay.state().status == "stopped"
    assert relay.state().last_error == "delete failed"
    assert relay._pending_text_retries == {}
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()
    modem_client.delete_message.assert_awaited_once_with(later.object_path)


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


async def test_start_drained_undecoded_sms_uses_retry_path(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    metrics: MetricsRegistry,
    sms_factory: SmsFactory,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEM_SMS_TEXT_UNDECODED_RETRY_DELAY_SECONDS", "5")
    original_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(relay, "_sleep", yield_once)
    listed = sms_factory(text="")
    decoded = listed.model_copy(update={"text": "decoded after drain"})
    modem_client.list_messages.return_value = [listed]
    modem_client.read_message.side_effect = [None, decoded]
    queue.enqueue = AsyncMock(wraps=queue.enqueue)

    await relay.start()
    try:
        await wait_until(lambda: modem_client.delete_message.await_count == 1)
        await wait_until(lambda: not relay._pending_text_retries)

        queue.enqueue.assert_awaited_once_with(decoded)
        modem_client.delete_message.assert_awaited_once_with(decoded.object_path)
        assert metric_value(metrics, "sms_received_total") == 1.0
        assert metric_value(metrics, "sms_text_undecoded_total") == 0.0
    finally:
        await relay.stop()
