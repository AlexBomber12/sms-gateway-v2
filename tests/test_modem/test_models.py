from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sms_gateway_v2.modem import IncomingSms, RegistrationState, SignalQuality


@pytest.mark.parametrize(
    ("dbus_value", "expected"),
    [
        (0, RegistrationState.IDLE),
        (1, RegistrationState.HOME),
        (2, RegistrationState.SEARCHING),
        (3, RegistrationState.DENIED),
        (4, RegistrationState.UNKNOWN),
        (5, RegistrationState.ROAMING),
        (6, RegistrationState.HOME_SMS_ONLY),
        (7, RegistrationState.ROAMING_SMS_ONLY),
        (8, RegistrationState.EMERGENCY_ONLY),
        (9, RegistrationState.HOME_CSFB_NOT_PREFERRED),
        (10, RegistrationState.ROAMING_CSFB_NOT_PREFERRED),
        (11, RegistrationState.ATTACHED_RLOS),
    ],
)
def test_registration_state_maps_known_dbus_values(
    dbus_value: int,
    expected: RegistrationState,
) -> None:
    assert RegistrationState.from_dbus_value(dbus_value) is expected


@pytest.mark.parametrize("dbus_value", [-1, 12, 999])
def test_registration_state_maps_unknown_dbus_values_to_unknown(dbus_value: int) -> None:
    assert RegistrationState.from_dbus_value(dbus_value) is RegistrationState.UNKNOWN


@pytest.mark.parametrize("percent", [0, 50, 100])
def test_signal_quality_accepts_percent_range(percent: int) -> None:
    signal = SignalQuality(percent=percent, recent=True)

    assert signal.percent == percent
    assert signal.captured_at.tzinfo is UTC


@pytest.mark.parametrize("percent", [-1, 101])
def test_signal_quality_rejects_out_of_range_percent(percent: int) -> None:
    with pytest.raises(ValidationError, match="percent must be between 0 and 100"):
        SignalQuality(percent=percent, recent=True)


def test_incoming_sms_parses_iso8601_timestamp() -> None:
    sms = IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="15551234567",
        text="hello",
        timestamp="2026-04-26T10:41:33+00:00",
        pdu_type="deliver",
    )

    assert sms.timestamp == datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC)
