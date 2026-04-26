from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import DuplicateMessage, Queue, QueueItem


async def test_queue_initialize_is_idempotent(queue: Queue) -> None:
    await queue.initialize()

    assert queue._dirs["pending"].is_dir()


async def test_queue_initialize_retries_when_dedup_initialize_fails(state_dir: Path) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)
    queue._dedup.initialize = AsyncMock(side_effect=[RuntimeError("boom"), None])

    with pytest.raises(RuntimeError, match="boom"):
        await queue.initialize()

    assert queue._dirs == {}
    await queue.initialize()

    assert queue._dirs["pending"].is_dir()
    assert queue._dedup.initialize.await_count == 2


async def test_enqueue_new_sms_returns_item_and_creates_pending_file(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    item = await queue.enqueue(sample_sms)

    assert isinstance(item, QueueItem)
    assert (queue._dirs["pending"] / f"{item.id}.json").exists()


async def test_enqueue_duplicate_sms_returns_none_and_does_not_create_second_file(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    first = await queue.enqueue(sample_sms)
    second = await queue.enqueue(sample_sms)

    assert first is not None
    assert second is None
    assert len(list(queue._dirs["pending"].glob("*.json"))) == 1
    assert await queue._dedup.is_duplicate(sample_sms.content_hash()) is True


async def test_enqueue_deduplicates_sms_without_timestamp(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    sms_without_timestamp = sample_sms.model_copy(update={"timestamp": None})

    first = await queue.enqueue(sms_without_timestamp)
    second = await queue.enqueue(sms_without_timestamp)

    assert first is not None
    assert second is None


async def test_enqueue_handles_duplicate_race_after_file_write(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    queue._dedup.record_new = AsyncMock(side_effect=DuplicateMessage("duplicate"))

    item = await queue.enqueue(sample_sms)

    assert item is None
    assert list(queue._dirs["pending"].glob("*.json")) == []


async def test_enqueue_hash_window_collapses_messages_in_same_window(state_dir: Path) -> None:
    queue = Queue(state_dir, dedup_window_minutes=5)
    await queue.initialize()
    try:
        first_sms = IncomingSms(
            object_path="/org/freedesktop/ModemManager1/SMS/1",
            number="+15551234567",
            text="hello",
            timestamp=sample_time(10, 41),
            pdu_type="deliver",
        )
        second_sms = first_sms.model_copy(
            update={
                "object_path": "/org/freedesktop/ModemManager1/SMS/2",
                "timestamp": sample_time(10, 44),
            }
        )

        first = await queue.enqueue(first_sms)
        second = await queue.enqueue(second_sms)

        assert first is not None
        assert second is None
    finally:
        await queue.close()


def sample_time(hour: int, minute: int) -> object:
    from datetime import UTC, datetime

    return datetime(2026, 4, 26, hour, minute, 33, tzinfo=UTC)
