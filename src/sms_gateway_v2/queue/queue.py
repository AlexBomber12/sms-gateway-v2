from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Collection
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue.dedup import DedupStore
from sms_gateway_v2.queue.exceptions import (
    DuplicateMessage,
    ItemNotFound,
    QueueCorrupted,
    QueueError,
)
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
        if dedup_window_minutes < 1:
            raise ValueError("dedup_window_minutes must be greater than or equal to 1")
        self._state_dir = state_dir
        self._dedup_window_minutes = dedup_window_minutes
        self._dirs: dict[str, Path] = {}
        self._dedup = DedupStore(state_dir / "dedup.db")
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        started_at = time.monotonic()
        async with self._lock:
            if self._dirs:
                return

            dirs = await asyncio.to_thread(ensure_state_dirs, self._state_dir)
            await self._dedup.initialize()
            await self._reconcile_startup_statuses(dirs, started_at=started_at)
            self._dirs = dirs

    async def close(self) -> None:
        async with self._lock:
            await self._dedup.close()
            self._dirs = {}

    async def enqueue(self, sms: IncomingSms) -> QueueItem | None:
        started_at = time.monotonic()
        item = self._with_content_hash(QueueItem.new(sms))
        content_hash = self._content_hash_for_item(item)
        async with self._lock:
            self._dirs_or_raise()
            if await self._dedup.is_duplicate(content_hash):
                logger.info(
                    "queue_enqueue_skipped_duplicate",
                    content_hash=content_hash,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return None

            duplicate_item = await self._find_pending_duplicate(
                content_hash,
                started_at=started_at,
            )
            if duplicate_item is not None:
                if self._content_hash_for_item(duplicate_item) == content_hash:
                    with suppress(DuplicateMessage):
                        await self._dedup.record_new(content_hash, duplicate_item.id)
                logger.info(
                    "queue_enqueue_skipped_pending_duplicate",
                    item_id=duplicate_item.id,
                    content_hash=content_hash,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return None

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

    async def claim_next(
        self,
        *,
        skip_item_ids: Collection[str] | None = None,
    ) -> QueueItem | None:
        started_at = time.monotonic()
        skipped_item_ids = frozenset(skip_item_ids or ())
        async with self._lock:
            self._dirs_or_raise()
            pending_paths = await asyncio.to_thread(list_items_sorted, self._dirs["pending"])
            for path in pending_paths:
                if path.stem in skipped_item_ids:
                    continue
                try:
                    item = await asyncio.to_thread(load_item, path)
                except QueueCorrupted as exc:
                    item_id = path.stem
                    logger.warning(
                        "queue_item_corrupted",
                        item_id=item_id,
                        path=str(path),
                        error=str(exc),
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    await asyncio.to_thread(
                        atomic_move,
                        item_id,
                        self._dirs["pending"],
                        self._dirs["failed"],
                    )
                    dedup_rows_removed = await self._dedup.delete_by_item_id(item_id)
                    logger.info(
                        "queue_corrupted_item_dedup_removed",
                        item_id=item_id,
                        dedup_rows_removed=dedup_rows_removed,
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    continue

                if item.id != path.stem:
                    item_id = path.stem
                    logger.warning(
                        "queue_item_corrupted",
                        item_id=item_id,
                        payload_item_id=item.id,
                        path=str(path),
                        error="queue item id does not match filename",
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    await asyncio.to_thread(
                        atomic_move,
                        item_id,
                        self._dirs["pending"],
                        self._dirs["failed"],
                    )
                    dedup_rows_removed = await self._dedup.delete_by_item_id(item_id)
                    if not await asyncio.to_thread(_queue_file_exists, item.id, self._dirs):
                        dedup_rows_removed += await self._dedup.delete_by_item_id(item.id)
                    logger.info(
                        "queue_corrupted_item_dedup_removed",
                        item_id=item_id,
                        payload_item_id=item.id,
                        dedup_rows_removed=dedup_rows_removed,
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    continue

                item_id = path.stem
                await asyncio.to_thread(
                    atomic_move,
                    item_id,
                    self._dirs["pending"],
                    self._dirs["processing"],
                )
                content_hash = self._content_hash_for_item(item)
                try:
                    await self._dedup.update_status(content_hash, ItemStatus.PROCESSING)
                except ItemNotFound:
                    await self._dedup.record_new(content_hash, item.id)
                    await self._dedup.update_status(content_hash, ItemStatus.PROCESSING)
                    logger.info(
                        "queue_item_dedup_repaired",
                        item_id=item_id,
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                logger.info(
                    "queue_item_claimed",
                    item_id=item_id,
                    elapsed_ms=_elapsed_ms(started_at),
                )
                return item

            logger.info("queue_claim_empty", elapsed_ms=_elapsed_ms(started_at))
            return None

    async def mark_sent(self, item: QueueItem) -> None:
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            await self._move_from_processing(item, self._dirs["sent"])
            await self._dedup.update_status(self._content_hash_for_item(item), ItemStatus.SENT)
            logger.info(
                "queue_item_sent",
                item_id=item.id,
                elapsed_ms=_elapsed_ms(started_at),
            )

    async def mark_failed(self, item: QueueItem) -> None:
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            await self._move_from_processing(item, self._dirs["failed"])
            await self._dedup.update_status(self._content_hash_for_item(item), ItemStatus.FAILED)
            logger.info(
                "queue_item_failed",
                item_id=item.id,
                attempts=item.attempts,
                elapsed_ms=_elapsed_ms(started_at),
            )

    async def move_back_to_pending(self, item: QueueItem) -> None:
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            try:
                await asyncio.to_thread(
                    atomic_move,
                    item.id,
                    self._dirs["processing"],
                    self._dirs["pending"],
                )
            except FileNotFoundError as exc:
                raise ItemNotFound(f"queue item not found in processing: {item.id}") from exc
            logger.info(
                "queue_item_moved_back_to_pending",
                item_id=item.id,
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
            self._dirs_or_raise()
            if not (self._dirs["processing"] / f"{item.id}.json").exists():
                raise ItemNotFound(f"queue item not found in processing: {item.id}")
            await asyncio.to_thread(save_item, updated, self._dirs["processing"])
            logger.info(
                "queue_item_attempt_updated",
                item_id=item.id,
                attempts=updated.attempts,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return updated

    async def recover_processing(self) -> int:
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            processing_paths = await asyncio.to_thread(
                list_items_sorted,
                self._dirs["processing"],
            )
            count = 0
            for path in processing_paths:
                await asyncio.to_thread(
                    atomic_move,
                    path.stem,
                    self._dirs["processing"],
                    self._dirs["pending"],
                )
                count += 1
            logger.info(
                "queue_recovery_completed",
                count=count,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return count

    async def requeue_failed(self, *, max_age_days: int) -> int:
        started_at = time.monotonic()
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        async with self._lock:
            self._dirs_or_raise()
            failed_paths = await asyncio.to_thread(list_items_sorted, self._dirs["failed"])
            count = 0
            for path in failed_paths:
                try:
                    item = await asyncio.to_thread(load_item, path)
                except QueueCorrupted as exc:
                    logger.warning(
                        "queue_failed_item_corrupted",
                        item_id=path.stem,
                        path=str(path),
                        error=str(exc),
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    continue
                if item.id != path.stem:
                    logger.warning(
                        "queue_failed_item_corrupted",
                        item_id=path.stem,
                        payload_item_id=item.id,
                        path=str(path),
                        error="queue item id does not match filename",
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    continue
                if item.first_seen_at >= cutoff:
                    await asyncio.to_thread(
                        atomic_move,
                        path.stem,
                        self._dirs["failed"],
                        self._dirs["pending"],
                    )
                    await self._dedup.update_status(
                        self._content_hash_for_item(item),
                        ItemStatus.PENDING,
                    )
                    count += 1
            logger.info(
                "queue_failed_requeued",
                count=count,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return count

    async def cleanup_sent(self, *, max_age_days: int) -> int:
        _validate_max_age_days(max_age_days)
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            item_ids = await self._dedup.item_ids_older_than(ItemStatus.SENT, max_age_days)
            known_item_ids = await self._dedup.item_ids()
            count = await asyncio.to_thread(
                _remove_item_files,
                self._dirs["sent"],
                item_ids,
            )
            count += await asyncio.to_thread(
                _remove_unknown_files_older_than,
                self._dirs["sent"],
                known_item_ids,
                max_age_days,
            )
            await self._dedup.purge_older_than(ItemStatus.SENT, max_age_days)
            logger.info(
                "queue_sent_cleaned",
                count=count,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return count

    async def cleanup_failed(self, *, max_age_days: int) -> int:
        _validate_max_age_days(max_age_days)
        started_at = time.monotonic()
        async with self._lock:
            self._dirs_or_raise()
            item_ids = await self._dedup.item_ids_older_than(ItemStatus.FAILED, max_age_days)
            known_item_ids = await self._dedup.item_ids()
            count = await asyncio.to_thread(
                _remove_item_files,
                self._dirs["failed"],
                item_ids,
            )
            count += await asyncio.to_thread(
                _remove_unknown_files_older_than,
                self._dirs["failed"],
                known_item_ids,
                max_age_days,
            )
            await self._dedup.purge_older_than(ItemStatus.FAILED, max_age_days)
            logger.info(
                "queue_failed_cleaned",
                count=count,
                elapsed_ms=_elapsed_ms(started_at),
            )
            return count

    def _content_hash_for_item(self, item: QueueItem) -> str:
        if item.content_hash is not None:
            return item.content_hash
        fallback_timestamp = item.first_seen_at
        return self._content_hash(item.sms, fallback_timestamp=fallback_timestamp)

    def content_hash_for_sms(
        self,
        sms: IncomingSms,
        *,
        fallback_timestamp: datetime | None = None,
    ) -> str:
        if fallback_timestamp is None:
            fallback_timestamp = datetime.now(UTC)
        return self._content_hash(sms, fallback_timestamp=fallback_timestamp)

    def _current_content_hash_for_item(self, item: QueueItem) -> str:
        return self._content_hash(item.sms, fallback_timestamp=item.first_seen_at)

    def _dirs_or_raise(self) -> dict[str, Path]:
        if not self._dirs:
            raise QueueError("queue is not initialized")
        return self._dirs

    def _with_content_hash(self, item: QueueItem) -> QueueItem:
        if item.content_hash is not None:
            return item
        return item.model_copy(update={"content_hash": self._content_hash_for_item(item)})

    def _content_hash(self, sms: IncomingSms, *, fallback_timestamp: datetime) -> str:
        timestamp = sms.timestamp or fallback_timestamp
        window_seconds = self._dedup_window_minutes * 60
        bucket_seconds = int(timestamp.timestamp()) // window_seconds * window_seconds
        bucket = datetime.fromtimestamp(bucket_seconds, tz=timestamp.tzinfo).isoformat()
        payload = f"{sms.number}|{sms.text}|{bucket}"
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _find_pending_duplicate(
        self,
        content_hash: str,
        *,
        started_at: float,
    ) -> QueueItem | None:
        pending_paths = await asyncio.to_thread(list_items_sorted, self._dirs["pending"])
        for path in pending_paths:
            try:
                item = await asyncio.to_thread(load_item, path)
            except QueueCorrupted:
                continue
            if item.id != path.stem:
                logger.warning(
                    "queue_pending_duplicate_scan_corrupted",
                    item_id=path.stem,
                    payload_item_id=item.id,
                    path=str(path),
                    error="queue item id does not match filename",
                    elapsed_ms=_elapsed_ms(started_at),
                )
                continue
            if content_hash in {
                self._content_hash_for_item(item),
                self._current_content_hash_for_item(item),
            }:
                return item
        return None

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

    async def _reconcile_startup_statuses(
        self,
        dirs: dict[str, Path],
        *,
        started_at: float,
    ) -> None:
        pending_count = await self._reconcile_status_dir(
            dirs["pending"],
            ItemStatus.PENDING,
            started_at=started_at,
        )
        sent_count = await self._reconcile_status_dir(
            dirs["sent"],
            ItemStatus.SENT,
            started_at=started_at,
        )
        failed_count = await self._reconcile_status_dir(
            dirs["failed"],
            ItemStatus.FAILED,
            started_at=started_at,
        )
        logger.info(
            "queue_startup_reconciliation_completed",
            pending_count=pending_count,
            sent_count=sent_count,
            failed_count=failed_count,
            elapsed_ms=_elapsed_ms(started_at),
        )

    async def _reconcile_status_dir(
        self,
        directory: Path,
        status: ItemStatus,
        *,
        started_at: float,
    ) -> int:
        paths = await asyncio.to_thread(list_items_sorted, directory)
        count = 0
        for path in paths:
            try:
                item = await asyncio.to_thread(load_item, path)
            except QueueCorrupted as exc:
                logger.warning(
                    "queue_reconciliation_item_corrupted",
                    item_id=path.stem,
                    status=status.value,
                    path=str(path),
                    error=str(exc),
                    elapsed_ms=_elapsed_ms(started_at),
                )
                continue
            if item.id != path.stem:
                logger.warning(
                    "queue_reconciliation_item_corrupted",
                    item_id=path.stem,
                    payload_item_id=item.id,
                    status=status.value,
                    path=str(path),
                    error="queue item id does not match filename",
                    elapsed_ms=_elapsed_ms(started_at),
                )
                continue
            await self._dedup.reconcile_status(
                self._content_hash_for_item(item),
                item.id,
                status,
            )
            count += 1
        return count


def _delete_pending_item(path: Path) -> None:
    load_item(path)
    path.unlink()


def _queue_file_exists(item_id: str, dirs: dict[str, Path]) -> bool:
    return any((directory / f"{item_id}.json").exists() for directory in dirs.values())


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _validate_max_age_days(max_age_days: int) -> None:
    if max_age_days < 0:
        raise ValueError("max_age_days must be greater than or equal to 0")


def _remove_item_files(directory: Path, item_ids: list[str]) -> int:
    count = 0
    for item_id in item_ids:
        path = directory / f"{item_id}.json"
        if path.exists():
            path.unlink()
            count += 1
    return count


def _remove_unknown_files_older_than(
    directory: Path,
    known_item_ids: set[str],
    max_age_days: int,
) -> int:
    cutoff = time.time() - (max_age_days * 86_400)
    count = 0
    for path in list_items_sorted(directory):
        if path.stem not in known_item_ids and path.stat().st_mtime < cutoff:
            path.unlink()
            count += 1
    return count
