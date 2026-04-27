from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import ItemStatus, Queue, QueueItem
from sms_gateway_v2.queue.paths import atomic_move, atomic_write_json
from tests.test_queue.helpers import content_hash


async def set_last_status_at(db_path: Path, content_hash: str, timestamp: float) -> None:
    async with aiosqlite.connect(db_path) as connection:
        await connection.execute(
            "UPDATE seen_messages SET last_status_at = ? WHERE content_hash = ?",
            (timestamp, content_hash),
        )
        await connection.commit()


async def dedup_count(db_path: Path, content_hash: str) -> int:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT COUNT(*) FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def dedup_status(db_path: Path, content_hash: str) -> str:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT status FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


def old_timestamp() -> float:
    return time.time() - (31 * 86_400)


async def enqueue_claim_and_mark_sent(queue: Queue, sms: IncomingSms) -> QueueItem:
    item = await queue.enqueue(sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    await queue.mark_sent(item)
    return item


async def enqueue_claim_and_mark_failed(queue: Queue, sms: IncomingSms) -> QueueItem:
    item = await queue.enqueue(sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    await queue.mark_failed(item)
    return item


async def test_requeue_failed_moves_young_failed_items_back_to_pending(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_claim_and_mark_failed(queue, sample_sms)

    requeued = await queue.requeue_failed(max_age_days=30)

    assert requeued == 1
    assert (queue._dirs["pending"] / f"{item.id}.json").exists()
    assert not (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert item.content_hash is not None
    assert await dedup_status(state_dir / "dedup.db", item.content_hash) == ItemStatus.PENDING.value


async def test_requeue_failed_leaves_old_failed_items_alone(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = QueueItem(
        id="1714149693000-0123456789abcdef0123456789abcdef",
        sms=sample_sms,
        first_seen_at=datetime.now(UTC) - timedelta(days=31),
    )
    atomic_write_json(item, queue._dirs)
    atomic_move(item.id, queue._dirs["pending"], queue._dirs["failed"])
    item_hash = content_hash(queue, sample_sms)
    await queue._dedup.record_new(item_hash, item.id)
    await queue._dedup.update_status(item_hash, ItemStatus.FAILED)

    requeued = await queue.requeue_failed(max_age_days=30)

    assert requeued == 0
    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert list(queue._dirs["pending"].glob("*.json")) == []


async def test_requeue_failed_skips_corrupt_files_and_requeues_later_valid_items(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    corrupt_path = queue._dirs["failed"] / "1714149692000-bad.json"
    corrupt_path.write_text("{bad-json", encoding="utf-8")
    valid_item = await enqueue_claim_and_mark_failed(
        queue,
        sample_sms.model_copy(update={"text": "valid failed"}),
    )

    requeued = await queue.requeue_failed(max_age_days=30)

    assert requeued == 1
    assert corrupt_path.exists()
    assert (queue._dirs["pending"] / f"{valid_item.id}.json").exists()
    assert not (queue._dirs["failed"] / f"{valid_item.id}.json").exists()


async def test_requeue_failed_skips_id_mismatch_and_requeues_later_valid_items(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    mismatched_file_id = "1714149692000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    payload_item_id = "1714149692001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    mismatched_item = QueueItem(
        id=payload_item_id,
        sms=sample_sms.model_copy(update={"text": "mismatched failed"}),
        first_seen_at=datetime.now(UTC),
    )
    mismatched_path = queue._dirs["failed"] / f"{mismatched_file_id}.json"
    mismatched_path.write_text(mismatched_item.to_json(), encoding="utf-8")
    valid_item = await enqueue_claim_and_mark_failed(
        queue,
        sample_sms.model_copy(update={"text": "valid after mismatch"}),
    )

    requeued = await queue.requeue_failed(max_age_days=30)

    assert requeued == 1
    assert mismatched_path.exists()
    assert (queue._dirs["pending"] / f"{valid_item.id}.json").exists()
    assert not (queue._dirs["failed"] / f"{valid_item.id}.json").exists()


async def test_cleanup_sent_removes_old_files_and_purges_dedup_rows(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    old_sms = sample_sms.model_copy(update={"text": "old sent"})
    recent_sms = sample_sms.model_copy(update={"text": "recent sent"})
    old_item = await enqueue_claim_and_mark_sent(queue, old_sms)
    recent_item = await enqueue_claim_and_mark_sent(queue, recent_sms)
    old_path = queue._dirs["sent"] / f"{old_item.id}.json"
    recent_path = queue._dirs["sent"] / f"{recent_item.id}.json"
    os.utime(old_path, (old_timestamp(), old_timestamp()))
    assert old_item.content_hash is not None
    assert recent_item.content_hash is not None
    await set_last_status_at(state_dir / "dedup.db", old_item.content_hash, old_timestamp())

    removed = await queue.cleanup_sent(max_age_days=30)

    assert removed == 1
    assert not old_path.exists()
    assert recent_path.exists()
    assert await dedup_count(state_dir / "dedup.db", old_item.content_hash) == 0
    assert await dedup_count(state_dir / "dedup.db", recent_item.content_hash) == 1


async def test_cleanup_sent_keeps_old_mtime_file_when_status_is_recent(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_claim_and_mark_sent(queue, sample_sms)
    path = queue._dirs["sent"] / f"{item.id}.json"
    os.utime(path, (old_timestamp(), old_timestamp()))

    removed = await queue.cleanup_sent(max_age_days=30)

    assert removed == 0
    assert path.exists()
    assert item.content_hash is not None
    assert await dedup_count(state_dir / "dedup.db", item.content_hash) == 1


async def test_cleanup_sent_rejects_negative_max_age_without_deleting(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_claim_and_mark_sent(queue, sample_sms)
    path = queue._dirs["sent"] / f"{item.id}.json"

    with pytest.raises(ValueError, match="max_age_days"):
        await queue.cleanup_sent(max_age_days=-1)

    assert path.exists()
    assert item.content_hash is not None
    assert await dedup_count(state_dir / "dedup.db", item.content_hash) == 1


async def test_cleanup_failed_removes_old_files_and_purges_dedup_rows(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    old_sms = sample_sms.model_copy(update={"text": "old failed"})
    recent_sms = sample_sms.model_copy(update={"text": "recent failed"})
    old_item = await enqueue_claim_and_mark_failed(queue, old_sms)
    recent_item = await enqueue_claim_and_mark_failed(queue, recent_sms)
    old_path = queue._dirs["failed"] / f"{old_item.id}.json"
    recent_path = queue._dirs["failed"] / f"{recent_item.id}.json"
    os.utime(old_path, (old_timestamp(), old_timestamp()))
    assert old_item.content_hash is not None
    assert recent_item.content_hash is not None
    await set_last_status_at(state_dir / "dedup.db", old_item.content_hash, old_timestamp())

    removed = await queue.cleanup_failed(max_age_days=30)

    assert removed == 1
    assert not old_path.exists()
    assert recent_path.exists()
    assert await dedup_count(state_dir / "dedup.db", old_item.content_hash) == 0
    assert await dedup_count(state_dir / "dedup.db", recent_item.content_hash) == 1


async def test_cleanup_failed_rejects_negative_max_age_without_deleting(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_claim_and_mark_failed(queue, sample_sms)
    path = queue._dirs["failed"] / f"{item.id}.json"

    with pytest.raises(ValueError, match="max_age_days"):
        await queue.cleanup_failed(max_age_days=-1)

    assert path.exists()
    assert item.content_hash is not None
    assert await dedup_count(state_dir / "dedup.db", item.content_hash) == 1


async def test_cleanup_failed_removes_old_unknown_corrupt_files(queue: Queue) -> None:
    corrupt_path = queue._dirs["failed"] / "1714149692000-bad.json"
    corrupt_path.write_text("{bad-json", encoding="utf-8")
    os.utime(corrupt_path, (old_timestamp(), old_timestamp()))

    removed = await queue.cleanup_failed(max_age_days=30)

    assert removed == 1
    assert not corrupt_path.exists()
