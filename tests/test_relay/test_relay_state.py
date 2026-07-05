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
    relay._sms_delete_failures_count = 2
    relay._last_delete_failure_at = datetime(2026, 4, 28, 12, 2, tzinfo=UTC)
    last_telegram_success_at = datetime(2026, 4, 28, 12, 3, tzinfo=UTC)
    relay.update_modem_health(
        state="registered",
        signal_percent=77,
        operator="MTS",
        registration="roaming",
    )
    relay.update_queue_counts(pending=4, failed=1)
    relay.record_telegram_success(last_telegram_success_at)

    state = relay.state()

    assert state.status == "running"
    assert state.started_at == started_at
    assert state.last_sms_received_at == last_sms_received_at
    assert state.last_error == "last failure"
    assert state.sms_delete_failures_count == 2
    assert state.last_delete_failure_at == datetime(2026, 4, 28, 12, 2, tzinfo=UTC)
    assert state.modem_state == "registered"
    assert state.modem_signal_percent == 77
    assert state.modem_operator == "MTS"
    assert state.modem_registration == "roaming"
    assert state.queue_pending_count == 4
    assert state.queue_failed_count == 1
    assert state.last_telegram_success_at == last_telegram_success_at
