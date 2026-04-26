from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sms_gateway_v2.modem import IncomingSms


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def sample_sms() -> IncomingSms:
    return IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="+15551234567",
        text="hello",
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )
