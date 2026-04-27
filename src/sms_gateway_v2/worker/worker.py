from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import structlog

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.queue import Queue, QueueItem
from sms_gateway_v2.queue.paths import atomic_move
from sms_gateway_v2.telegram import (
    TelegramAuthError,
    TelegramClient,
    TelegramError,
    TelegramMessage,
    TelegramRateLimited,
    TelegramTransportError,
)

logger = structlog.get_logger(__name__)


class DeliveryWorker:
    def __init__(
        self,
        queue: Queue,
        telegram_client: TelegramClient,
        telegram_chat_id: str,
        metrics: MetricsRegistry,
        retry_schedule_seconds: tuple[int, ...],
    ) -> None:
        self._queue = queue
        self._telegram_client = telegram_client
        self._telegram_chat_id = telegram_chat_id
        self._metrics = metrics
        self._retry_schedule_seconds = retry_schedule_seconds
        self._stop_event: asyncio.Event = asyncio.Event()
        self._wakeup_event: asyncio.Event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                work_done = await self._process_one_pending_item()
            except Exception as exc:
                logger.exception("delivery_worker_iteration_failed", error=str(exc))
                work_done = False

            if self._stop_event.is_set():
                break

            if not work_done:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wakeup_event.wait(),
                        timeout=self._compute_idle_timeout(),
                    )
                self._wakeup_event.clear()

    async def _process_one_pending_item(self) -> bool:
        skipped_item_ids: set[str] = set()
        while True:
            item = await self._queue.claim_next(skip_item_ids=skipped_item_ids)
            if item is None:
                return False

            now = datetime.now(UTC)
            if item.next_retry_at is None or now >= item.next_retry_at:
                break

            skipped_item_ids.add(item.id)
            await self._move_processing_to_pending(item)

        message = TelegramMessage.from_sms(
            chat_id=self._telegram_chat_id,
            number=item.sms.number,
            text=item.sms.text,
        )
        try:
            with self._metrics.telegram_send_duration_seconds.time():
                await self._telegram_client.send_message(message)
        except TelegramAuthError:
            logger.warning(
                "delivery_failed_permanent",
                item_id=item.id,
                attempt=item.attempts,
                attempts_used=item.attempts + 1,
                reason="auth_error",
            )
            self._metrics.sms_failed_total.inc()
            self._metrics.telegram_send_total.labels(result="failure").inc()
            self._metrics.telegram_send_failures_total.labels(reason="auth_error").inc()
            await self._queue.mark_failed(item)
            return True
        except TelegramError as exc:
            reason = _telegram_failure_reason(exc)
            attempts_used = item.attempts + 1
            if attempts_used >= len(self._retry_schedule_seconds):
                logger.warning(
                    "delivery_failed_permanent",
                    item_id=item.id,
                    attempt=item.attempts,
                    attempts_used=attempts_used,
                    reason="exhausted",
                )
                self._metrics.sms_failed_total.inc()
                self._metrics.telegram_send_total.labels(result="failure").inc()
                self._metrics.telegram_send_failures_total.labels(reason="exhausted").inc()
                await self._queue.mark_failed(item)
                return True

            delay_seconds = self._retry_schedule_seconds[item.attempts]
            next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            updated = await self._queue.update_attempt(item, next_retry_at=next_retry_at)
            await self._move_processing_to_pending(updated)
            self._metrics.telegram_send_total.labels(result="failure").inc()
            self._metrics.telegram_send_failures_total.labels(reason=reason).inc()
            logger.info(
                "delivery_retry_scheduled",
                item_id=item.id,
                attempt=item.attempts,
                attempts_used=attempts_used,
                delay_seconds=delay_seconds,
                reason=reason,
            )
            return True
        except Exception as exc:
            logger.exception(
                "delivery_unexpected_error",
                item_id=item.id,
                attempt=item.attempts,
                attempts_used=item.attempts + 1,
                error=str(exc),
            )
            self._metrics.sms_failed_total.inc()
            self._metrics.telegram_send_failures_total.labels(reason="exhausted").inc()
            await self._queue.mark_failed(item)
            return True

        await self._queue.mark_sent(item)
        self._metrics.sms_delivered_total.inc()
        self._metrics.telegram_send_total.labels(result="success").inc()
        self._metrics.last_telegram_success_seconds.set(time.time())
        logger.info(
            "delivery_succeeded",
            item_id=item.id,
            attempt=item.attempts,
            attempts_used=item.attempts + 1,
        )
        return True

    def stop(self) -> None:
        self._stop_event.set()

    def wakeup(self) -> None:
        self._wakeup_event.set()

    def _compute_idle_timeout(self) -> float:
        return 60.0

    async def _move_processing_to_pending(self, item: QueueItem) -> None:
        dirs = self._queue._dirs_or_raise()
        await asyncio.to_thread(atomic_move, item.id, dirs["processing"], dirs["pending"])


def _telegram_failure_reason(error: TelegramError) -> str:
    if isinstance(error, TelegramRateLimited):
        return "rate_limited"
    if isinstance(error, TelegramTransportError):
        return "transport_error"
    return "exhausted"
