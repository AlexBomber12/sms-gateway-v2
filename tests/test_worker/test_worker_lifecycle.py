from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from unittest.mock import AsyncMock, MagicMock

import pytest

import sms_gateway_v2.worker as worker_api
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.worker import DeliveryResult, DeliveryWorker
from tests.test_worker.helpers import metric_value


async def test_stop_set_before_run_exits_without_processing(
    telegram_client: MagicMock,
    worker: DeliveryWorker,
) -> None:
    worker.stop()

    await worker.run()

    telegram_client.send_message.assert_not_awaited()


async def test_stop_unblocks_idle_wait(worker: DeliveryWorker) -> None:
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0)

    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)


async def test_run_empty_queue_handles_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
    worker: DeliveryWorker,
) -> None:
    async def raise_timeout(awaitable: Awaitable[bool], *, timeout: float) -> bool:
        assert timeout == 60.0
        worker.stop()
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise TimeoutError

    monkeypatch.setattr("sms_gateway_v2.worker.worker.asyncio.wait_for", raise_timeout)

    await worker.run()


async def test_run_logs_unexpected_iteration_error_without_delivery_metrics(
    monkeypatch: pytest.MonkeyPatch,
    metrics: MetricsRegistry,
    worker: DeliveryWorker,
) -> None:
    async def raise_once() -> bool:
        worker.stop()
        raise RuntimeError("boom")

    logger = MagicMock()
    monkeypatch.setattr(worker, "_process_one_pending_item", raise_once)
    monkeypatch.setattr("sms_gateway_v2.worker.worker.logger", logger)

    await worker.run()

    assert metric_value(metrics, "sms_failed_total") == 0.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "exhausted"}) == 0.0
    logger.exception.assert_called_once_with("delivery_worker_iteration_failed", error="boom")


async def test_process_one_pending_item_returns_false_for_empty_queue(
    worker: DeliveryWorker,
) -> None:
    assert await worker._process_one_pending_item() is False


def test_worker_public_api_exports_only_delivery_worker_and_delivery_result() -> None:
    assert worker_api.__all__ == ["DeliveryResult", "DeliveryWorker"]
    assert worker_api.DeliveryWorker is DeliveryWorker
    assert worker_api.DeliveryResult is DeliveryResult


def test_delivery_worker_has_only_expected_sync_public_methods() -> None:
    public_methods = {
        name: value
        for name, value in DeliveryWorker.__dict__.items()
        if not name.startswith("_") and callable(value)
    }

    assert set(public_methods) == {"reset", "run", "stop", "wakeup"}
    assert not inspect.iscoroutinefunction(public_methods["reset"])
    assert inspect.iscoroutinefunction(public_methods["run"])
    assert not inspect.iscoroutinefunction(public_methods["stop"])
    assert not inspect.iscoroutinefunction(public_methods["wakeup"])


def test_reset_clears_stop_and_wakeup_events(worker: DeliveryWorker) -> None:
    worker.stop()

    worker.reset()

    assert not worker._stop_event.is_set()
    assert not worker._wakeup_event.is_set()


async def test_process_one_pending_item_propagates_queue_claim_errors_to_run(
    monkeypatch: pytest.MonkeyPatch,
    metrics: MetricsRegistry,
    queue: Queue,
    worker: DeliveryWorker,
) -> None:
    async def fail_claim(**_kwargs: object) -> None:
        worker.stop()
        raise RuntimeError("claim failed")

    queue.claim_next = AsyncMock(side_effect=fail_claim)
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.worker.worker.logger", logger)

    await worker.run()

    assert metric_value(metrics, "sms_failed_total") == 0.0
    assert metric_value(metrics, "telegram_send_failures_total", {"reason": "exhausted"}) == 0.0
    logger.exception.assert_called_once_with(
        "delivery_worker_iteration_failed",
        error="claim failed",
    )
