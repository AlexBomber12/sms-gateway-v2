from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.modem import IncomingSms, ModemManagerUnavailable
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.relay import SmsRelay
from sms_gateway_v2.relay.exceptions import RelayError
from sms_gateway_v2.worker import DeliveryWorker
from tests.test_relay.conftest import FireAddedSignal


async def test_start_on_fresh_relay_sets_running_then_stop_returns_stopped(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    queue.initialize = AsyncMock(wraps=queue.initialize)
    queue.close = AsyncMock(wraps=queue.close)

    await relay.start()
    try:
        state = relay.state()

        assert state.status == "running"
        assert state.started_at is not None
        modem_client.connect.assert_awaited_once()
        queue.initialize.assert_awaited_once()
        modem_client.watch_added.assert_awaited_once()
        assert relay._worker_task is not None
        assert not relay._worker_task.done()
    finally:
        await relay.stop()

    stopped_state = relay.state()
    assert stopped_state.status == "stopped"
    assert stopped_state.started_at is None
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()


async def test_start_raises_relay_error_if_already_running(relay: SmsRelay) -> None:
    await relay.start()
    try:
        with pytest.raises(RelayError, match="relay is already started or in transition"):
            await relay.start()
    finally:
        await relay.stop()


async def test_stop_is_idempotent(relay: SmsRelay) -> None:
    await relay.start()

    await relay.stop()
    await relay.stop()

    assert relay.state().status == "stopped"


async def test_stop_while_never_started_is_noop(
    relay: SmsRelay,
    modem_client: MagicMock,
) -> None:
    await relay.stop()

    assert relay.state().status == "stopped"
    modem_client.disconnect.assert_not_awaited()


async def test_relay_restart_resets_worker_and_processes_new_sms(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    sample_sms: IncomingSms,
    fire_added_signal: FireAddedSignal,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
) -> None:
    await relay.start()
    await relay.stop()

    await relay.start()
    try:
        modem_client.read_message.return_value = sample_sms

        await fire_added_signal(sample_sms.object_path)
        await wait_until(lambda: bool(list(queue._dirs["sent"].glob("*.json"))))

        assert relay.state().status == "running"
    finally:
        await relay.stop()


async def test_start_propagates_modem_manager_unavailable_and_resets_status(
    relay: SmsRelay,
    modem_client: MagicMock,
) -> None:
    modem_client.connect.side_effect = ModemManagerUnavailable("dbus down")

    with pytest.raises(ModemManagerUnavailable, match="dbus down"):
        await relay.start()

    state = relay.state()
    assert state.status == "stopped"
    assert state.last_error == "dbus down"


async def test_start_rolls_back_when_queue_initialize_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    queue.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(RuntimeError, match="init failed"):
        await relay.start()

    assert relay.state().status == "stopped"
    assert relay.state().last_error == "init failed"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_not_awaited()


async def test_start_rolls_back_and_allows_retry_when_recover_processing_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    queue.recover_processing = AsyncMock(side_effect=[RuntimeError("recover failed"), 0])
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(RuntimeError, match="recover failed"):
        await relay.start()

    assert relay.state().status == "stopped"
    assert relay.state().last_error == "recover failed"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()

    await relay.start()
    try:
        assert relay.state().status == "running"
    finally:
        await relay.stop()


async def test_start_rolls_back_on_cancellation_after_queue_initialize(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    queue.recover_processing = AsyncMock(side_effect=asyncio.CancelledError)
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(asyncio.CancelledError):
        await relay.start()

    assert relay.state().status == "stopped"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()


async def test_stop_waits_for_in_flight_start_and_leaves_relay_stopped(
    relay: SmsRelay,
    modem_client: MagicMock,
    wait_until: Callable[[Callable[[], bool]], Awaitable[None]],
) -> None:
    continue_start = asyncio.Event()

    async def block_watch_added(callback: Callable[[str], Awaitable[None]]) -> None:
        await continue_start.wait()

    modem_client.watch_added = AsyncMock(side_effect=block_watch_added)

    start_task = asyncio.create_task(relay.start())
    await wait_until(lambda: relay.state().status == "starting")

    stop_task = asyncio.create_task(relay.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()

    continue_start.set()
    await start_task
    await stop_task

    assert relay.state().status == "stopped"
    modem_client.disconnect.assert_awaited_once()


async def test_stop_cancellation_preserves_disconnect_and_queue_close(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    disconnect_started = asyncio.Event()
    allow_disconnect = asyncio.Event()

    async def block_disconnect() -> None:
        disconnect_started.set()
        await allow_disconnect.wait()

    await relay.start()
    modem_client.disconnect = AsyncMock(side_effect=block_disconnect)
    queue.close = AsyncMock(wraps=queue.close)

    stop_task = asyncio.create_task(relay.stop())
    await disconnect_started.wait()
    stop_task.cancel()
    await asyncio.sleep(0)

    assert not stop_task.done()

    allow_disconnect.set()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert relay.state().status == "stopped"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()


async def test_start_calls_recover_processing_and_logs_count(
    relay: SmsRelay,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)
    queue.recover_processing = AsyncMock(return_value=3)

    await relay.start()
    try:
        queue.recover_processing.assert_awaited_once()
        logger.info.assert_any_call("relay_recovery_completed", count=3)
    finally:
        await relay.stop()


async def test_stop_cancels_worker_task_after_timeout(
    relay: SmsRelay,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_wait_for(awaitable: object, timeout: float) -> None:
        assert awaitable is relay._worker_task
        assert timeout == 5.0
        raise TimeoutError

    monkeypatch.setattr("sms_gateway_v2.relay.relay.asyncio.wait_for", fake_wait_for)

    await relay.start()
    task = relay._worker_task
    assert task is not None

    await relay.stop()

    assert task.cancelled()
    assert relay.state().status == "stopped"


async def test_stop_cleans_up_when_worker_task_was_already_cancelled(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    await relay.start()
    queue.close = AsyncMock(wraps=queue.close)
    task = relay._worker_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    await relay.stop()

    assert task.cancelled()
    assert relay.state().status == "stopped"
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()


async def test_stop_finalizes_state_and_closes_queue_when_disconnect_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    await relay.start()
    modem_client.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
    queue.close = AsyncMock(wraps=queue.close)

    with pytest.raises(RuntimeError, match="disconnect failed"):
        await relay.stop()

    assert relay.state().status == "stopped"
    assert relay.state().started_at is None
    assert relay.state().last_error == "disconnect failed"
    assert relay._worker_task is None
    modem_client.disconnect.assert_awaited_once()
    queue.close.assert_awaited_once()


async def test_stop_finalizes_state_when_queue_close_fails(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
) -> None:
    original_close = queue.close
    await relay.start()
    queue.close = AsyncMock(side_effect=RuntimeError("close failed"))

    try:
        with pytest.raises(RuntimeError, match="close failed"):
            await relay.stop()

        assert relay.state().status == "stopped"
        assert relay.state().started_at is None
        assert relay.state().last_error == "close failed"
        assert relay._worker_task is None
        modem_client.disconnect.assert_awaited_once()
        queue.close.assert_awaited_once()
    finally:
        queue.close = original_close
        await queue.close()


async def test_stop_without_worker_task_still_disconnects_and_closes(
    relay: SmsRelay,
    modem_client: MagicMock,
    queue: Queue,
    worker: DeliveryWorker,
) -> None:
    await relay.start()
    relay._worker_task = None

    await relay.stop()

    assert worker._stop_event.is_set()
    modem_client.disconnect.assert_awaited_once()
    assert queue._dirs == {}
