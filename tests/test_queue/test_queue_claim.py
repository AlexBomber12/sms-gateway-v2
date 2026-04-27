from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import ItemStatus, Queue, QueueItem
from sms_gateway_v2.queue.paths import atomic_write_json
from tests.test_queue.helpers import content_hash


def make_item(sample_sms: IncomingSms, item_id: str, *, text: str = "hello") -> QueueItem:
    return QueueItem(
        id=item_id,
        sms=sample_sms.model_copy(update={"text": text}),
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )


async def dedup_status(db_path: Path, content_hash: str) -> str:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT status FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


async def dedup_row_count(db_path: Path) -> int:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute("SELECT COUNT(*) FROM seen_messages")
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def test_claim_next_returns_none_on_empty_queue(queue: Queue) -> None:
    assert await queue.claim_next() is None


async def test_claim_next_returns_oldest_item_by_id_sort_and_moves_to_processing(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    newer = make_item(sample_sms, "1714149693001-0123456789abcdef0123456789abcdef", text="newer")
    older = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef", text="older")
    atomic_write_json(newer, queue._dirs)
    atomic_write_json(older, queue._dirs)
    await queue._dedup.record_new(content_hash(queue, older.sms), older.id)
    await queue._dedup.record_new(content_hash(queue, newer.sms), newer.id)

    claimed = await queue.claim_next()

    assert claimed == older
    assert (queue._dirs["processing"] / f"{older.id}.json").exists()
    assert (queue._dirs["pending"] / f"{newer.id}.json").exists()
    assert not (queue._dirs["pending"] / f"{older.id}.json").exists()


async def test_claim_next_on_corrupt_file_moves_it_to_failed_and_tries_next(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    corrupt_path = queue._dirs["pending"] / "1714149692000-bad.json"
    corrupt_path.write_text("{bad-json", encoding="utf-8")
    valid = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    atomic_write_json(valid, queue._dirs)
    await queue._dedup.record_new(content_hash(queue, valid.sms), valid.id)

    claimed = await queue.claim_next()

    assert claimed == valid
    assert not corrupt_path.exists()
    assert (queue._dirs["failed"] / corrupt_path.name).exists()
    assert (queue._dirs["processing"] / f"{valid.id}.json").exists()


async def test_claim_next_on_corrupt_enqueued_file_removes_dedup_row(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None
    pending_path = queue._dirs["pending"] / f"{item.id}.json"
    pending_path.write_text("{bad-json", encoding="utf-8")

    claimed = await queue.claim_next()

    assert claimed is None
    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert item.content_hash is not None
    assert await queue._dedup.is_duplicate(item.content_hash) is False
    assert await queue.enqueue(sample_sms) is not None


async def test_claim_next_on_malformed_content_hash_moves_to_failed_and_tries_next(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    corrupt = make_item(
        sample_sms,
        "1714149692000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        text="malformed hash",
    ).model_copy(update={"content_hash": "a" * 64})
    corrupt_path = queue._dirs["pending"] / f"{corrupt.id}.json"
    corrupt_path.write_text(corrupt.to_json().replace("a" * 64, "not-a-sha256"), encoding="utf-8")
    valid = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    atomic_write_json(valid, queue._dirs)
    await queue._dedup.record_new(content_hash(queue, valid.sms), valid.id)

    claimed = await queue.claim_next()

    assert claimed == valid
    assert not corrupt_path.exists()
    assert (queue._dirs["failed"] / corrupt_path.name).exists()
    assert (queue._dirs["processing"] / f"{valid.id}.json").exists()


async def test_claim_next_on_id_mismatch_moves_file_to_failed_and_tries_next(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    mismatched_file_id = "1714149692000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    payload_item_id = "1714149692001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    mismatched_item = make_item(sample_sms, payload_item_id, text="mismatched")
    mismatched_path = queue._dirs["pending"] / f"{mismatched_file_id}.json"
    mismatched_path.write_text(mismatched_item.to_json(), encoding="utf-8")
    mismatched_hash = content_hash(queue, mismatched_item.sms)
    await queue._dedup.record_new(mismatched_hash, mismatched_file_id)
    valid = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    atomic_write_json(valid, queue._dirs)
    await queue._dedup.record_new(content_hash(queue, valid.sms), valid.id)

    claimed = await queue.claim_next()

    assert claimed == valid
    assert not mismatched_path.exists()
    assert (queue._dirs["failed"] / mismatched_path.name).exists()
    assert (queue._dirs["processing"] / f"{valid.id}.json").exists()
    assert await queue._dedup.is_duplicate(mismatched_hash) is False


async def test_claim_next_on_id_mismatch_removes_payload_id_dedup_row(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    mismatched_file_id = "1714149692000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    payload_item_id = "1714149692001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    mismatched_item = make_item(sample_sms, payload_item_id)
    mismatched_path = queue._dirs["pending"] / f"{mismatched_file_id}.json"
    mismatched_path.write_text(mismatched_item.to_json(), encoding="utf-8")
    mismatched_hash = content_hash(queue, mismatched_item.sms)
    await queue._dedup.record_new(mismatched_hash, payload_item_id)

    claimed = await queue.claim_next()

    assert claimed is None
    assert (queue._dirs["failed"] / mismatched_path.name).exists()
    assert await queue._dedup.is_duplicate(mismatched_hash) is False
    assert await queue.enqueue(sample_sms) is not None


async def test_claim_next_updates_dedup_status_to_processing(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await queue.enqueue(sample_sms)
    assert item is not None

    claimed = await queue.claim_next()

    assert claimed == item
    assert item.content_hash is not None
    assert (
        await dedup_status(state_dir / "dedup.db", item.content_hash) == ItemStatus.PROCESSING.value
    )


async def test_claim_and_mark_use_persisted_content_hash_after_window_change(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    original_queue = Queue(state_dir, dedup_window_minutes=5)
    await original_queue.initialize()
    item = await original_queue.enqueue(sample_sms)
    assert item is not None
    assert item.content_hash is not None
    await original_queue.close()

    reopened_queue = Queue(state_dir, dedup_window_minutes=1)
    await reopened_queue.initialize()
    try:
        claimed = await reopened_queue.claim_next()
        assert claimed is not None
        assert claimed.content_hash == item.content_hash
        await reopened_queue.mark_sent(claimed)

        assert await dedup_row_count(state_dir / "dedup.db") == 1
        assert await dedup_status(state_dir / "dedup.db", item.content_hash) == (
            ItemStatus.SENT.value
        )
    finally:
        await reopened_queue.close()


async def test_claim_next_repairs_missing_dedup_row_for_pending_item(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    atomic_write_json(item, queue._dirs)

    claimed = await queue.claim_next()

    assert claimed == item
    assert (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert await dedup_status(state_dir / "dedup.db", content_hash(queue, sample_sms)) == (
        ItemStatus.PROCESSING.value
    )
