from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import QueueItem
from sms_gateway_v2.queue.exceptions import QueueCorrupted


def test_queue_item_new_generates_timestamp_uuid_id(sample_sms: IncomingSms) -> None:
    item = QueueItem.new(sample_sms)

    assert re.fullmatch(r"\d{13}-[0-9a-f]{32}", item.id)
    assert item.sms == sample_sms
    assert item.first_seen_at.tzinfo == UTC
    assert item.attempts == 0
    assert item.last_attempt_at is None
    assert item.next_retry_at is None


def test_queue_item_json_round_trips(sample_sms: IncomingSms) -> None:
    item = QueueItem(
        id="1714149693000-0123456789abcdef0123456789abcdef",
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )

    restored = QueueItem.from_json(item.to_json())

    assert restored == item


def test_queue_item_from_json_raises_queue_corrupted_on_invalid_json() -> None:
    with pytest.raises(QueueCorrupted, match="invalid queue item JSON"):
        QueueItem.from_json("{not-json")
