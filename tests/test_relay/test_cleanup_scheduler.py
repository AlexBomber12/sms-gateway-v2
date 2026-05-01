from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.queue import Queue
from sms_gateway_v2.relay import CleanupScheduler
from sms_gateway_v2.relay import cleanup_scheduler as cleanup_scheduler_module


def _make_queue(
    *,
    requeue: int = 0,
    sent: int = 0,
    failed: int = 0,
) -> MagicMock:
    queue = MagicMock(spec=Queue)
    queue.requeue_failed = AsyncMock(return_value=requeue)
    queue.cleanup_sent = AsyncMock(return_value=sent)
    queue.cleanup_failed = AsyncMock(return_value=failed)
    return queue


async def test_run_cleanup_invokes_methods_in_order_with_retention() -> None:
    queue = _make_queue(requeue=2, sent=5, failed=3)
    scheduler = CleanupScheduler(
        queue=queue,
        sent_retention_days=7,
        failed_retention_days=14,
        interval_seconds=3600.0,
    )

    await scheduler._run_cleanup()

    queue.requeue_failed.assert_awaited_once_with(max_age_days=14)
    queue.cleanup_sent.assert_awaited_once_with(max_age_days=7)
    queue.cleanup_failed.assert_awaited_once_with(max_age_days=14)
    assert queue.method_calls[0][0] == "requeue_failed"
    assert queue.method_calls[1][0] == "cleanup_sent"
    assert queue.method_calls[2][0] == "cleanup_failed"


async def test_run_loops_until_stop_called(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _make_queue()
    scheduler = CleanupScheduler(
        queue=queue,
        sent_retention_days=30,
        failed_retention_days=30,
        interval_seconds=3600.0,
    )

    iterations = 0

    async def stopping_cleanup() -> None:
        nonlocal iterations
        iterations += 1
        scheduler.stop()

    monkeypatch.setattr(scheduler, "_run_cleanup", stopping_cleanup)

    await scheduler.run()

    assert iterations == 1


async def test_run_cleanup_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _make_queue()
    queue.cleanup_sent = AsyncMock(side_effect=RuntimeError("disk full"))
    scheduler = CleanupScheduler(
        queue=queue,
        sent_retention_days=30,
        failed_retention_days=30,
        interval_seconds=3600.0,
    )

    log_events: list[tuple[str, dict[str, Any]]] = []

    class CapturingLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(cleanup_scheduler_module, "logger", CapturingLogger())

    await scheduler._run_cleanup()

    assert any(event == "cleanup_iteration_failed" for event, _ in log_events)


async def test_run_continues_after_iteration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _make_queue()
    queue.cleanup_sent = AsyncMock(side_effect=RuntimeError("transient"))
    scheduler = CleanupScheduler(
        queue=queue,
        sent_retention_days=30,
        failed_retention_days=30,
        interval_seconds=3600.0,
    )

    iterations = 0
    original_run_cleanup = scheduler._run_cleanup

    async def counting_cleanup() -> None:
        nonlocal iterations
        iterations += 1
        await original_run_cleanup()
        scheduler.stop()

    monkeypatch.setattr(scheduler, "_run_cleanup", counting_cleanup)

    await scheduler.run()

    assert iterations == 1
    queue.requeue_failed.assert_awaited_once()
    queue.cleanup_sent.assert_awaited_once()
