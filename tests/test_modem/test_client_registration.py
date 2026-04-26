from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, RegistrationState

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"


@pytest.mark.parametrize(
    ("dbus_value", "expected"),
    [
        (0, RegistrationState.IDLE),
        (1, RegistrationState.HOME),
        (2, RegistrationState.SEARCHING),
        (3, RegistrationState.DENIED),
        (4, RegistrationState.UNKNOWN),
        (5, RegistrationState.ROAMING),
    ],
)
async def test_get_registration_state_maps_known_values(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    dbus_value: int,
    expected: RegistrationState,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    fake_modem_proxy.modem_3gpp.get_registration_state.return_value = dbus_value
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    assert await client.get_registration_state() is expected


async def test_get_registration_state_maps_unknown_integer_to_unknown(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    fake_modem_proxy.modem_3gpp.get_registration_state.return_value = 99
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    assert await client.get_registration_state() is RegistrationState.UNKNOWN


async def test_get_registration_state_wraps_modem_lookup_failures(
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
        await client.get_registration_state()

    assert exc.value.__cause__ is error
