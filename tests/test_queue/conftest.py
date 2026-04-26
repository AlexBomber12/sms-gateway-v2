from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
async def queue(state_dir: Path) -> AsyncIterator[Queue]:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    try:
        yield queue
    finally:
        await queue.close()


@pytest.fixture
def sample_sms() -> IncomingSms:
    return IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="+15551234567",
        text="hello",
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )
