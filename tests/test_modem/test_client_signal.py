from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"


async def test_get_signal_quality_returns_signal_quality_on_happy_path(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    signal = await client.get_signal_quality()

    assert signal.percent == 76
    assert signal.recent is True


async def test_get_signal_quality_captured_at_is_utc_datetime(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    signal = await client.get_signal_quality()

    assert isinstance(signal.captured_at, datetime)
    assert signal.captured_at.tzinfo is UTC


async def test_get_signal_quality_wraps_modem_lookup_failures(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "ModemManager restarted")
    fake_bus.introspect.side_effect = error
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query modem object {MODEM_PATH}",
    ) as exc:
        await client.get_signal_quality()

    assert exc.value.__cause__ is error
