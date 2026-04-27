from __future__ import annotations

from datetime import UTC, datetime

from sms_gateway_v2.relay import SmsRelay


def test_state_returns_current_snapshot(relay: SmsRelay) -> None:
    started_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    last_sms_received_at = datetime(2026, 4, 28, 12, 1, tzinfo=UTC)
    relay._status = "running"
    relay._started_at = started_at
    relay._last_sms_received_at = last_sms_received_at
    relay._last_error = "last failure"

    state = relay.state()

    assert state.status == "running"
    assert state.started_at == started_at
    assert state.last_sms_received_at == last_sms_received_at
    assert state.last_error == "last failure"
