from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import sms_gateway_v2.relay as relay_api
from sms_gateway_v2.relay import (
    CleanupScheduler,
    ModemWatchdog,
    RelayError,
    RelayNotRunning,
    RelayState,
    SmsRelay,
    build_relay,
)


def test_relay_public_api_exports_state_and_relay_only() -> None:
    assert relay_api.__all__ == [
        "CleanupScheduler",
        "ModemWatchdog",
        "RelayError",
        "RelayNotRunning",
        "RelayState",
        "SmsRelay",
        "build_relay",
    ]
    assert relay_api.CleanupScheduler is CleanupScheduler
    assert relay_api.ModemWatchdog is ModemWatchdog
    assert relay_api.RelayError is RelayError
    assert relay_api.RelayNotRunning is RelayNotRunning
    assert relay_api.RelayState is RelayState
    assert relay_api.SmsRelay is SmsRelay
    assert relay_api.build_relay is build_relay


def test_relay_state_construction() -> None:
    started_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    last_sms_received_at = datetime(2026, 4, 28, 12, 1, tzinfo=UTC)

    state = RelayState(
        status="running",
        started_at=started_at,
        last_sms_received_at=last_sms_received_at,
        last_error="last failure",
        ignored="value",
    )

    assert state.status == "running"
    assert state.started_at == started_at
    assert state.last_sms_received_at == last_sms_received_at
    assert state.last_error == "last failure"
    assert not hasattr(state, "ignored")


@pytest.mark.parametrize("status", ["stopped", "starting", "running", "stopping"])
def test_relay_state_accepts_valid_status_values(status: str) -> None:
    state = RelayState(
        status=status,
        started_at=None,
        last_sms_received_at=None,
        last_error=None,
    )

    assert state.status == status


def test_relay_state_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        RelayState(
            status="paused",
            started_at=None,
            last_sms_received_at=None,
            last_error=None,
        )


def test_relay_state_is_frozen() -> None:
    state = RelayState(
        status="stopped",
        started_at=None,
        last_sms_received_at=None,
        last_error=None,
    )

    field_name = "status"
    with pytest.raises(ValidationError):
        setattr(state, field_name, "running")
