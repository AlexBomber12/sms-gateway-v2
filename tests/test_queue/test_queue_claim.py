from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import ItemStatus, Queue, QueueItem
from sms_gateway_v2.queue.paths import atomic_write_json


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
    await queue._dedup.record_new(older.sms.content_hash(), older.id)
    await queue._dedup.record_new(newer.sms.content_hash(), newer.id)

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
    await queue._dedup.record_new(valid.sms.content_hash(), valid.id)

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
    assert await queue._dedup.is_duplicate(sample_sms.content_hash()) is False
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
    assert await dedup_status(state_dir / "dedup.db", sample_sms.content_hash()) == (
        ItemStatus.PROCESSING.value
    )
