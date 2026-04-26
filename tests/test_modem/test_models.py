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


def test_incoming_sms_content_hash_uses_minute_precision() -> None:
    sms = IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="15551234567",
        text="hello",
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )

    assert sms.content_hash() == "38706b17c99e35295f7eb3bde14feaeb858567470842cb7c5b9948032f3971e3"


@pytest.mark.parametrize(
    ("number", "text", "timestamp", "expected"),
    [
        (
            "15550000000",
            "hello",
            datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
            "252e7a54712f3e0d18ce3aecdeb10a1513bccb24f5db2eff98bb722f1b1c6c7e",
        ),
        (
            "15551234567",
            "goodbye",
            datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
            "7d2a507e2591d76af681918e3e5ff833932d9b41a5784d640008f7ea2586fc87",
        ),
        (
            "15551234567",
            "hello",
            datetime(2026, 4, 26, 10, 42, 33, tzinfo=UTC),
            "7959d32409402e35daef4fb90193356698611956770903f0d31d06958c06a878",
        ),
        (
            "15551234567",
            "hello",
            None,
            "7934e0283c8d9354b389c21020b991f91a138943997b55b581762a26d4fcfccd",
        ),
    ],
)
def test_incoming_sms_content_hash_changes_for_identity_fields(
    number: str,
    text: str,
    timestamp: datetime | None,
    expected: str,
) -> None:
    sms = IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number=number,
        text=text,
        timestamp=timestamp,
        pdu_type="deliver",
    )

    assert sms.content_hash() == expected
