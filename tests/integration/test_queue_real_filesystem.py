from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue


def make_sms(object_path: str, text: str) -> IncomingSms:
    return IncomingSms(
        object_path=object_path,
        number="+15551234567",
        text=text,
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )


@pytest.mark.integration
async def test_queue_survives_simulated_crash_recovery(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    first = await queue.enqueue(make_sms("/org/freedesktop/ModemManager1/SMS/1", "first"))
    second = await queue.enqueue(make_sms("/org/freedesktop/ModemManager1/SMS/2", "second"))
    assert first is not None
    assert second is not None
    assert await queue.claim_next() == first
    assert await queue.claim_next() == second
    await queue.close()

    recovered_queue = Queue(state_dir, dedup_window_minutes=1)
    await recovered_queue.initialize()
    try:
        recovered = await recovered_queue.recover_processing()
        first_reclaimed = await recovered_queue.claim_next()
        second_reclaimed = await recovered_queue.claim_next()

        assert recovered == 2
        assert first_reclaimed is not None
        assert second_reclaimed is not None
        assert {first_reclaimed.id, second_reclaimed.id} == {first.id, second.id}
    finally:
        await recovered_queue.close()
