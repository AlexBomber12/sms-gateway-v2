from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog

from sms_gateway_v2.queue import Queue

logger = structlog.get_logger(__name__)


class CleanupScheduler:
    def __init__(
        self,
        queue: Queue,
        sent_retention_days: int,
        failed_retention_days: int,
        interval_seconds: float,
    ) -> None:
        self._queue = queue
        self._sent_retention_days = sent_retention_days
        self._failed_retention_days = failed_retention_days
        self._interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self._run_cleanup()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )

    async def _run_cleanup(self) -> None:
        try:
            requeued = await self._queue.requeue_failed(
                max_age_days=self._failed_retention_days,
            )
            logger.info("cleanup_requeue_failed", count=requeued)
            sent_cleaned = await self._queue.cleanup_sent(
                max_age_days=self._sent_retention_days,
            )
            logger.info("cleanup_sent", count=sent_cleaned)
            failed_cleaned = await self._queue.cleanup_failed(
                max_age_days=self._failed_retention_days,
            )
            logger.info("cleanup_failed", count=failed_cleaned)
        except Exception as exc:
            logger.warning("cleanup_iteration_failed", error=str(exc))

    def stop(self) -> None:
        self._stop_event.set()
