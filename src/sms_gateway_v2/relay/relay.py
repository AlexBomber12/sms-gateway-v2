from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime

import structlog

from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.modem import (
    IncomingSms,
    MessageDeleteFailed,
    ModemManagerClient,
)
from sms_gateway_v2.queue import Queue
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.worker import DeliveryWorker

from .exceptions import RelayError
from .models import RelayState, RelayStatus

logger = structlog.get_logger(__name__)


class SmsRelay:
    def __init__(
        self,
        modem_client: ModemManagerClient,
        queue: Queue,
        telegram_client: TelegramClient,
        worker: DeliveryWorker,
        metrics: MetricsRegistry,
    ) -> None:
        self._modem_client = modem_client
        self._queue = queue
        self._telegram_client = telegram_client
        self._worker = worker
        self._metrics = metrics
        self._status: RelayStatus = "stopped"
        self._started_at: datetime | None = None
        self._last_sms_received_at: datetime | None = None
        self._last_error: str | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._sms_handler_lock: asyncio.Lock = asyncio.Lock()

    @property
    def telegram_client(self) -> TelegramClient:
        return self._telegram_client

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._start_locked()

    async def stop(self) -> None:
        stop_task = asyncio.create_task(self._stop_locked())
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await stop_task
            raise

    async def _start_locked(self) -> None:
        if self._status != "stopped":
            raise RelayError("relay is already started or in transition")

        self._status = "starting"
        connected = False
        queue_initialized = False
        try:
            await self._modem_client.connect()
            connected = True
            await self._queue.initialize()
            queue_initialized = True
            recovered = await self._queue.recover_processing()
            logger.info("relay_recovery_completed", count=recovered)
            await self._modem_client.watch_added(self._on_new_sms)
            await self._drain_existing_messages()
            self._worker.reset()
            self._worker_task = asyncio.create_task(self._worker.run())
            self._worker_task.add_done_callback(self._log_worker_task_result)
            self._started_at = datetime.now(UTC)
            self._status = "running"
        except asyncio.CancelledError as exc:
            await self._rollback_startup_failure(
                exc,
                connected=connected,
                queue_initialized=queue_initialized,
            )
            raise
        except Exception as exc:
            await self._rollback_startup_failure(
                exc,
                connected=connected,
                queue_initialized=queue_initialized,
            )
            raise
        logger.info(
            "relay_started",
            started_at=self._started_at,
            recovered=recovered,
        )

    async def _stop_locked(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_started_relay()

    async def _stop_started_relay(self) -> None:
        if self._status in {"stopped", "stopping"}:
            return

        self._status = "stopping"
        stop_error: Exception | None = None
        try:
            self._worker.stop()
            await self._wait_for_worker_shutdown()
            stop_error = await self._disconnect_and_close()
        finally:
            self._status = "stopped"
            self._worker_task = None
            self._started_at = None
            logger.info("relay_stopped")

        if stop_error is not None:
            raise stop_error

    async def _wait_for_worker_shutdown(self) -> None:
        if self._worker_task is None:
            return

        try:
            await asyncio.wait_for(self._worker_task, timeout=5.0)
        except TimeoutError:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._worker_task
        except asyncio.CancelledError:
            logger.info("relay_worker_task_already_cancelled")
        except Exception:
            pass

    async def _disconnect_and_close(self) -> Exception | None:
        stop_error: Exception | None = None
        try:
            await self._modem_client.disconnect()
        except Exception as exc:
            stop_error = exc
            self._last_error = str(exc)
            logger.exception("relay_disconnect_failed", error=str(exc))

        try:
            await self._queue.close()
        except Exception as exc:
            if stop_error is None:
                stop_error = exc
            self._last_error = str(exc)
            logger.exception("relay_queue_close_failed", error=str(exc))

        return stop_error

    async def _on_new_sms(self, sms_path: str) -> None:
        try:
            async with self._sms_handler_lock:
                await self._process_sms_path(sms_path, fail_on_delete_error=False)
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception(
                "relay_sms_handler_error",
                sms_path=sms_path,
                error=str(exc),
            )

    async def _drain_existing_messages(self) -> None:
        messages = await self._modem_client.list_messages()
        for message in messages:
            async with self._sms_handler_lock:
                await self._process_sms(message, fail_on_delete_error=True)
        logger.info("relay_drain_completed", count_drained=len(messages))

    def state(self) -> RelayState:
        return RelayState(
            status=self._status,
            started_at=self._started_at,
            last_sms_received_at=self._last_sms_received_at,
            last_error=self._last_error,
        )

    async def _process_sms_path(self, sms_path: str, *, fail_on_delete_error: bool) -> None:
        sms = await self._modem_client.read_message(sms_path)
        if sms is None:
            logger.warning("relay_sms_path_not_found", sms_path=sms_path)
            return

        await self._process_sms(sms, fail_on_delete_error=fail_on_delete_error)

    async def _process_sms(self, sms: IncomingSms, *, fail_on_delete_error: bool) -> None:
        sms_path = sms.object_path
        item = await self._queue.enqueue(sms)
        if item is None:
            self._metrics.sms_dedup_hits_total.inc()
            logger.info("relay_sms_skipped_duplicate", sms_path=sms_path)
        else:
            self._metrics.sms_received_total.inc()
            self._metrics.last_sms_received_seconds.set(time.time())
            self._last_sms_received_at = datetime.now(UTC)
            logger.info(
                "relay_sms_enqueued",
                item_id=item.id,
                sms_path=sms_path,
            )

        self._worker.wakeup()
        try:
            await self._modem_client.delete_message(sms_path)
        except MessageDeleteFailed as exc:
            logger.warning(
                "relay_sms_delete_failed",
                sms_path=sms_path,
                error=str(exc),
            )
            if fail_on_delete_error:
                raise

    async def _rollback_startup_failure(
        self,
        exc: BaseException,
        *,
        connected: bool,
        queue_initialized: bool,
    ) -> None:
        self._last_error = str(exc)
        if connected:
            with suppress(Exception):
                await self._modem_client.disconnect()
        if queue_initialized:
            with suppress(Exception):
                await self._queue.close()
        self._worker_task = None
        self._started_at = None
        self._status = "stopped"

    def _log_worker_task_result(self, task: asyncio.Future[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            logger.exception("relay_worker_task_failed", error=str(exc))
