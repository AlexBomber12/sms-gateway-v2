from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue.dedup import DedupStore
from sms_gateway_v2.queue.exceptions import DuplicateMessage, ItemNotFound, QueueCorrupted
from sms_gateway_v2.queue.models import ItemStatus, QueueItem
from sms_gateway_v2.queue.paths import (
    atomic_move,
    atomic_write_json,
    ensure_state_dirs,
    list_items_sorted,
    load_item,
    save_item,
)

logger = structlog.get_logger(__name__)


class Queue:
    def __init__(self, state_dir: Path, dedup_window_minutes: int) -> None:
        self._state_dir = state_dir
        self._dedup_window_minutes = dedup_window_minutes
        self._dirs: dict[str, Path] = {}
        self._dedup = DedupStore(state_dir / "dedup.db")
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._dirs:
                return

            self._dirs = await asyncio.to_thread(ensure_state_dirs, self._state_dir)
            await self._dedup.initialize()

    async def close(self) -> None:
        await self._dedup.close()

    async def enqueue(self, sms: IncomingSms) -> QueueItem | None:
        started_at = time.monotonic()
        content_hash = self._content_hash(sms)
        async with self._lock:
            if await self._dedup.is_duplicate(content_hash):
                logger.info(
                    "queue_enqueue_skipped_duplicate",
                    content_hash=content_hash,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return None

            item = QueueItem.new(sms)
            await asyncio.to_thread(atomic_write_json, item, self._dirs)
            try:
                await self._dedup.record_new(content_hash, item.id)
            except DuplicateMessage:
                pending_path = self._dirs["pending"] / f"{item.id}.json"
                await asyncio.to_thread(_delete_pending_item, pending_path)
                logger.info(
                    "queue_enqueue_skipped_duplicate",
                    item_id=item.id,
                    content_hash=content_hash,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return None

            logger.info(
                "queue_item_enqueued",
                item_id=item.id,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return item

    async def claim_next(self) -> QueueItem | None:
        started_at = time.monotonic()
        async with self._lock:
            pending_paths = await asyncio.to_thread(list_items_sorted, self._dirs["pending"])
            for path in pending_paths:
                try:
                    item = await asyncio.to_thread(load_item, path)
                except QueueCorrupted as exc:
                    logger.warning(
                        "queue_item_corrupted",
                        path=str(path),
                        error=str(exc),
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    await asyncio.to_thread(
                        atomic_move,
                        path.stem,
                        self._dirs["pending"],
                        self._dirs["failed"],
                    )
                    continue

                await asyncio.to_thread(
                    atomic_move,
                    item.id,
                    self._dirs["pending"],
                    self._dirs["processing"],
                )
                await self._dedup.update_status(self._content_hash(item.sms), ItemStatus.PROCESSING)
                logger.info(
                    "queue_item_claimed",
                    item_id=item.id,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return item

            logger.info("queue_claim_empty", elapsed_ms=_elapsed_ms(started_at))
            return None

    async def mark_sent(self, item: QueueItem) -> None:
        started_at = time.monotonic()
        async with self._lock:
            await self._move_from_processing(item, self._dirs["sent"])
            await self._dedup.update_status(self._content_hash(item.sms), ItemStatus.SENT)
            logger.info(
                "queue_item_sent",
                item_id=item.id,
                elapsed_ms=_elapsed_ms(started_at),
            )

    async def mark_failed(self, item: QueueItem) -> None:
        started_at = time.monotonic()
        async with self._lock:
            await self._move_from_processing(item, self._dirs["failed"])
            await self._dedup.update_status(self._content_hash(item.sms), ItemStatus.FAILED)
            logger.info(
                "queue_item_failed",
                item_id=item.id,
                attempts=item.attempts,
                elapsed_ms=_elapsed_ms(started_at),
            )

    async def update_attempt(self, item: QueueItem, *, next_retry_at: datetime) -> QueueItem:
        started_at = time.monotonic()
        updated = item.model_copy(
            update={
                "attempts": item.attempts + 1,
                "last_attempt_at": datetime.now(UTC),
                "next_retry_at": next_retry_at,
            }
        )
        async with self._lock:
            await asyncio.to_thread(save_item, updated, self._dirs["processing"])
            logger.info(
                "queue_item_attempt_updated",
                item_id=item.id,
                attempts=updated.attempts,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return updated

    def _content_hash(self, sms: IncomingSms) -> str:
        if sms.timestamp is None:
            bucket = ""
        else:
            window_seconds = max(self._dedup_window_minutes, 1) * 60
            bucket_seconds = int(sms.timestamp.timestamp()) // window_seconds * window_seconds
            bucket = datetime.fromtimestamp(bucket_seconds, tz=sms.timestamp.tzinfo).isoformat()
        payload = f"{sms.number}|{sms.text}|{bucket}"
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _move_from_processing(self, item: QueueItem, dest_dir: Path) -> None:
        try:
            await asyncio.to_thread(
                atomic_move,
                item.id,
                self._dirs["processing"],
                dest_dir,
            )
        except FileNotFoundError as exc:
            raise ItemNotFound(f"queue item not found in processing: {item.id}") from exc


def _delete_pending_item(path: Path) -> None:
    load_item(path)
    path.unlink()


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
