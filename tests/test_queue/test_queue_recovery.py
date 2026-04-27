from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import ItemStatus, Queue, QueueItem
from sms_gateway_v2.queue.paths import atomic_move


async def claim_enqueued(queue: Queue, sms: IncomingSms) -> str:
    item = await queue.enqueue(sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    return item.id


async def dedup_status(db_path: Path, content_hash: str) -> str:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT status FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


async def test_recover_processing_returns_zero_on_empty_processing(queue: Queue) -> None:
    assert await queue.recover_processing() == 0


async def test_recover_processing_moves_all_items_back_to_pending(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    first_id = await claim_enqueued(queue, sample_sms)
    second_id = await claim_enqueued(queue, sample_sms.model_copy(update={"text": "second"}))

    recovered = await queue.recover_processing()

    assert recovered == 2
    assert sorted(path.name for path in queue._dirs["pending"].glob("*.json")) == [
        f"{first_id}.json",
        f"{second_id}.json",
    ]
    assert list(queue._dirs["processing"].glob("*.json")) == []


async def test_initialize_reconciles_terminal_file_with_processing_dedup_status(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    item_id = await claim_enqueued(queue, sample_sms)
    atomic_move(item_id, queue._dirs["processing"], queue._dirs["sent"])
    assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
        ItemStatus.PROCESSING.value
    )
    await queue.close()

    reopened = Queue(state_dir, dedup_window_minutes=1)
    await reopened.initialize()
    try:
        assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
            ItemStatus.SENT.value
        )
    finally:
        await reopened.close()


async def test_initialize_reconciles_failed_file_with_processing_dedup_status(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    item_id = await claim_enqueued(queue, sample_sms)
    atomic_move(item_id, queue._dirs["processing"], queue._dirs["failed"])
    assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
        ItemStatus.PROCESSING.value
    )
    await queue.close()

    reopened = Queue(state_dir, dedup_window_minutes=1)
    await reopened.initialize()
    try:
        assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
            ItemStatus.FAILED.value
        )
    finally:
        await reopened.close()


async def test_initialize_reconciles_pending_file_with_failed_dedup_status(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    item = await queue.enqueue(sample_sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    await queue.mark_failed(item)
    atomic_move(item.id, queue._dirs["failed"], queue._dirs["pending"])
    assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
        ItemStatus.FAILED.value
    )
    await queue.close()

    reopened = Queue(state_dir, dedup_window_minutes=1)
    await reopened.initialize()
    try:
        assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
            ItemStatus.PENDING.value
        )
    finally:
        await reopened.close()


async def test_initialize_skips_corrupt_terminal_files(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    corrupt_path = queue._dirs["sent"] / "1714149692000-bad.json"
    corrupt_path.write_text("{bad-json", encoding="utf-8")
    mismatched_file_id = "1714149692000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    mismatched_item = QueueItem(
        id="1714149692001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )
    mismatched_path = queue._dirs["failed"] / f"{mismatched_file_id}.json"
    mismatched_path.write_text(mismatched_item.to_json(), encoding="utf-8")
    await queue.close()

    reopened = Queue(state_dir, dedup_window_minutes=1)
    await reopened.initialize()
    try:
        assert corrupt_path.exists()
        assert mismatched_path.exists()
    finally:
        await reopened.close()
