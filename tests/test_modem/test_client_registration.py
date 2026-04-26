from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sms_gateway_v2.modem import ModemManagerClient, RegistrationState

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
