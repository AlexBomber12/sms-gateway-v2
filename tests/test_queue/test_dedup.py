from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from sms_gateway_v2.queue.dedup import DedupStore
from sms_gateway_v2.queue.exceptions import DuplicateMessage, ItemNotFound, QueueError
from sms_gateway_v2.queue.models import ItemStatus


async def fetch_one(
    db_path: Path,
    query: str,
    params: tuple[object, ...] = (),
) -> tuple[object, ...]:
    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(query, params)
        row = await cursor.fetchone()
        assert row is not None
        return row


async def set_last_status_at(db_path: Path, content_hash: str, timestamp: float) -> None:
    async with aiosqlite.connect(db_path) as connection:
        await connection.execute(
            "UPDATE seen_messages SET last_status_at = ? WHERE content_hash = ?",
            (timestamp, content_hash),
        )
        await connection.commit()


async def test_initialize_creates_table_and_sets_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    store = DedupStore(db_path)

    await store.initialize()
    await store.initialize()

    journal_mode = await fetch_one(db_path, "PRAGMA journal_mode")
    table = await fetch_one(
        db_path,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'seen_messages'",
    )
    assert journal_mode == ("wal",)
    assert table == ("seen_messages",)
    await store.close()


async def test_close_is_idempotent(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "dedup.db")

    await store.initialize()
    await store.close()
    await store.close()


async def test_methods_raise_before_initialize(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "dedup.db")

    with pytest.raises(QueueError, match="not initialized"):
        await store.is_duplicate("hash")


async def test_is_duplicate_returns_false_for_new_hash_and_true_after_record_new(
    tmp_path: Path,
) -> None:
    store = DedupStore(tmp_path / "dedup.db")
    await store.initialize()

    assert await store.is_duplicate("hash") is False
    await store.record_new("hash", "item-id")

    assert await store.is_duplicate("hash") is True
    await store.close()


async def test_record_new_raises_duplicate_message_for_existing_hash(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "dedup.db")
    await store.initialize()
    await store.record_new("hash", "item-id")

    with pytest.raises(DuplicateMessage, match="hash"):
        await store.record_new("hash", "other-item-id")

    await store.close()


async def test_update_status_updates_existing_row(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    store = DedupStore(db_path)
    await store.initialize()
    await store.record_new("hash", "item-id")

    await store.update_status("hash", ItemStatus.SENT)

    row = await fetch_one(
        db_path,
        "SELECT status FROM seen_messages WHERE content_hash = ?",
        ("hash",),
    )
    assert row == (ItemStatus.SENT.value,)
    await store.close()


async def test_update_status_raises_item_not_found_for_missing_hash(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "dedup.db")
    await store.initialize()

    with pytest.raises(ItemNotFound, match="missing"):
        await store.update_status("missing", ItemStatus.SENT)

    await store.close()


async def test_delete_by_item_id_deletes_matching_row_and_returns_count(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    store = DedupStore(db_path)
    await store.initialize()
    await store.record_new("hash", "item-id")

    deleted = await store.delete_by_item_id("item-id")
    missing_deleted = await store.delete_by_item_id("missing-item-id")

    remaining = await fetch_one(
        db_path,
        "SELECT COUNT(*) FROM seen_messages WHERE content_hash = ?",
        ("hash",),
    )
    assert deleted == 1
    assert missing_deleted == 0
    assert remaining == (0,)
    await store.close()


async def test_item_ids_older_than_returns_only_matching_status_older_than_cutoff(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dedup.db"
    old_timestamp = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    recent_timestamp = datetime.now(UTC).timestamp()
    store = DedupStore(db_path)
    await store.initialize()
    await store.record_new("old-sent", "item-1")
    await store.record_new("recent-sent", "item-2")
    await store.record_new("old-failed", "item-3")
    await store.update_status("old-sent", ItemStatus.SENT)
    await store.update_status("recent-sent", ItemStatus.SENT)
    await store.update_status("old-failed", ItemStatus.FAILED)
    await set_last_status_at(db_path, "old-sent", old_timestamp)
    await set_last_status_at(db_path, "recent-sent", recent_timestamp)
    await set_last_status_at(db_path, "old-failed", old_timestamp)

    item_ids = await store.item_ids_older_than(ItemStatus.SENT, 30)

    assert item_ids == ["item-1"]
    await store.close()


async def test_purge_older_than_deletes_only_matching_status_older_than_cutoff(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dedup.db"
    old_timestamp = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    recent_timestamp = datetime.now(UTC).timestamp()
    store = DedupStore(db_path)
    await store.initialize()
    await store.record_new("old-sent", "item-1")
    await store.record_new("recent-sent", "item-2")
    await store.record_new("old-failed", "item-3")
    await store.update_status("old-sent", ItemStatus.SENT)
    await store.update_status("recent-sent", ItemStatus.SENT)
    await store.update_status("old-failed", ItemStatus.FAILED)
    await set_last_status_at(db_path, "old-sent", old_timestamp)
    await set_last_status_at(db_path, "recent-sent", recent_timestamp)
    await set_last_status_at(db_path, "old-failed", old_timestamp)

    deleted = await store.purge_older_than(ItemStatus.SENT, 30)

    remaining_sent = await fetch_one(
        db_path,
        "SELECT COUNT(*) FROM seen_messages WHERE status = ?",
        (ItemStatus.SENT.value,),
    )
    remaining_failed = await fetch_one(
        db_path,
        "SELECT COUNT(*) FROM seen_messages WHERE status = ?",
        (ItemStatus.FAILED.value,),
    )
    assert deleted == 1
    assert remaining_sent == (1,)
    assert remaining_failed == (1,)
    await store.close()
