from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError
from dbus_fast.errors import InterfaceNotFoundError

import sms_gateway_v2.modem.client as client_module
from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"


@pytest.mark.parametrize(
    ("raw_state", "expected_state"),
    [
        (3, "disabled"),
        ("registered", "registered"),
        (999, "unknown"),
    ],
)
async def test_get_modem_state_returns_decoded_state(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    raw_state: int | str,
    expected_state: str,
) -> None:
    fake_modem_props.get_state.return_value = raw_state
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    modem_state = await client.get_modem_state()

    assert modem_state == expected_state


async def test_get_modem_state_uses_single_property_read(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.get_modem_state()

    fake_modem_props.get_state.assert_awaited_once_with()
    fake_modem_props.get_sim.assert_not_awaited()
    fake_modem_proxy.modem_3gpp.get_registration_state.assert_not_awaited()


async def test_enable_calls_dbus_enable_true(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    monkeypatch.setattr(client_module.time, "monotonic", lambda: 10.0)
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.enable()

    fake_modem_props.call_enable.assert_awaited_once_with(True)
    captured_logger.info.assert_any_call(
        "modem_enable_called",
        duration_seconds=0.0,
        modem_path=MODEM_PATH,
    )


async def test_enable_translates_dbus_error(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.NoReply", "modem timed out")
    fake_modem_props.call_enable.side_effect = error
    fake_bus.get_proxy_object.return_value = fake_modem_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to enable modem at {MODEM_PATH}",
    ) as exc:
        await client.enable()

    assert exc.value.__cause__ is error


async def test_enable_recovers_from_interface_not_found_on_cached_path(
    fake_bus: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_modem = MagicMock()
    refreshed_modem.call_enable = AsyncMock(return_value=None)
    refreshed_proxy = MagicMock()
    refreshed_proxy.get_interface.return_value = refreshed_modem
    fake_bus.get_proxy_object.side_effect = [stale_proxy, refreshed_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)
    client._resubscribe_added_watchers = AsyncMock()

    await client.enable()

    refreshed_modem.call_enable.assert_awaited_once_with(True)
    client.find_modem.assert_awaited_once_with()
    client._resubscribe_added_watchers.assert_awaited_once_with(REFRESHED_MODEM_PATH)
