from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import structlog

from sms_gateway_v2.metrics.registry import MetricsRegistry
from sms_gateway_v2.queue import Queue

logger = structlog.get_logger(__name__)

_STATE_NAMES = ("pending", "processing", "sent", "failed")
QueueCountsCallback = Callable[[int, int], None]


class QueueGaugeUpdater:
    def __init__(
        self,
        queue: Queue,
        metrics: MetricsRegistry,
        interval_seconds: float,
        queue_counts_callback: QueueCountsCallback | None = None,
    ) -> None:
        self._queue = queue
        self._metrics = metrics
        self._interval_seconds = interval_seconds
        self._queue_counts_callback = queue_counts_callback
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self._update_gauges()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )

    async def _update_gauges(self) -> None:
        try:
            dirs = self._queue.state_dirs()
            counts: dict[str, int] = {}
            for name in _STATE_NAMES:
                count = await asyncio.to_thread(_count_json_files, dirs[name])
                counts[name] = count
                gauge = getattr(self._metrics, f"queue_{name}_count")
                gauge.set(count)
            if self._queue_counts_callback is not None:
                self._queue_counts_callback(counts["pending"], counts["failed"])
        except Exception as exc:
            logger.warning("gauge_update_failed", error=str(exc))

    def stop(self) -> None:
        self._stop_event.set()


def _count_json_files(directory: Path) -> int:
    return sum(1 for _ in directory.glob("*.json"))
