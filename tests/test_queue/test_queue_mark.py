from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import ItemNotFound, ItemStatus, Queue, QueueItem
from sms_gateway_v2.queue.paths import load_item


async def dedup_status(db_path: Path, content_hash: str) -> str:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT status FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


async def enqueue_and_claim(queue: Queue, sms: IncomingSms) -> QueueItem:
    item = await queue.enqueue(sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    return claimed


async def test_mark_sent_moves_item_from_processing_to_sent_and_updates_dedup(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_and_claim(queue, sample_sms)

    await queue.mark_sent(item)

    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert (queue._dirs["sent"] / f"{item.id}.json").exists()
    assert item.content_hash is not None
    assert await dedup_status(state_dir / "dedup.db", item.content_hash) == ItemStatus.SENT.value


async def test_mark_failed_moves_item_to_failed_and_updates_dedup(
    queue: Queue,
    sample_sms: IncomingSms,
    state_dir: Path,
) -> None:
    item = await enqueue_and_claim(queue, sample_sms)

    await queue.mark_failed(item)

    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert (queue._dirs["failed"] / f"{item.id}.json").exists()
    assert item.content_hash is not None
    assert await dedup_status(state_dir / "dedup.db", item.content_hash) == ItemStatus.FAILED.value


async def test_mark_failed_persists_permanently_failed_flag(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = await enqueue_and_claim(queue, sample_sms)

    await queue.mark_failed(item, permanently_failed=True)

    persisted = load_item(queue._dirs["failed"] / f"{item.id}.json")
    assert persisted.permanently_failed is True


async def test_move_back_to_pending_moves_item(queue: Queue, sample_sms: IncomingSms) -> None:
    item = await enqueue_and_claim(queue, sample_sms)

    await queue.move_back_to_pending(item)

    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert (queue._dirs["pending"] / f"{item.id}.json").exists()


async def test_mark_sent_raises_item_not_found_when_processing_file_is_missing(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    missing = QueueItem(
        id="1714149693000-0123456789abcdef0123456789abcdef",
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )

    with pytest.raises(ItemNotFound, match=missing.id):
        await queue.mark_sent(missing)


async def test_mark_failed_raises_item_not_found_when_processing_file_is_missing(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    missing = QueueItem(
        id="1714149693000-0123456789abcdef0123456789abcdef",
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )

    with pytest.raises(ItemNotFound, match=missing.id):
        await queue.mark_failed(missing)


async def test_move_back_to_pending_raises_item_not_found_for_missing_processing_file(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    missing = QueueItem(
        id="1714149693000-0123456789abcdef0123456789abcdef",
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )

    with pytest.raises(ItemNotFound, match=missing.id):
        await queue.move_back_to_pending(missing)


async def test_update_attempt_increments_attempts_and_persists_in_processing(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = await enqueue_and_claim(queue, sample_sms)
    next_retry_at = datetime(2026, 4, 26, 10, 46, 33, tzinfo=UTC)

    updated = await queue.update_attempt(item, next_retry_at=next_retry_at)

    persisted = load_item(queue._dirs["processing"] / f"{item.id}.json")
    assert updated.id == item.id
    assert updated.attempts == 1
    assert updated.last_attempt_at is not None
    assert updated.next_retry_at == next_retry_at
    assert persisted == updated


async def test_update_attempt_raises_item_not_found_for_stale_item(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = await enqueue_and_claim(queue, sample_sms)
    recovered = await queue.recover_processing()
    assert recovered == 1

    with pytest.raises(ItemNotFound, match=item.id):
        await queue.update_attempt(
            item,
            next_retry_at=datetime(2026, 4, 26, 10, 46, 33, tzinfo=UTC),
        )

    assert not (queue._dirs["processing"] / f"{item.id}.json").exists()
    assert (queue._dirs["pending"] / f"{item.id}.json").exists()
