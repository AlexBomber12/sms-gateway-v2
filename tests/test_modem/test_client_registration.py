from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError
from dbus_fast.errors import InterfaceNotFoundError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, RegistrationState

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
MODEM_3GPP_INTERFACE = "org.freedesktop.ModemManager1.Modem.Modem3gpp"


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


async def test_get_registration_state_recovers_from_interface_not_found_on_cached_path(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_3GPP_INTERFACE)
    fake_modem_proxy.modem_3gpp.get_registration_state.return_value = 7
    fake_bus.get_proxy_object.side_effect = [stale_proxy, fake_modem_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)
    client._resubscribe_added_watchers = AsyncMock()

    registration = await client.get_registration_state()

    assert registration is RegistrationState.ROAMING_SMS_ONLY
    assert client._modem_path == REFRESHED_MODEM_PATH
    client.find_modem.assert_awaited_once_with()
    client._resubscribe_added_watchers.assert_awaited_once_with(REFRESHED_MODEM_PATH)


async def test_get_operator_name_returns_current_network_operator(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    fake_modem_proxy.modem_3gpp.get_operator_name.return_value = "vodafone IT"
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    assert await client.get_operator_name() == "vodafone IT"
