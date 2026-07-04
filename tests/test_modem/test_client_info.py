from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import (
    ModemError,
    ModemInfo,
    ModemManagerClient,
    ModemManagerUnavailable,
    RegistrationState,
)

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
SIM_PATH = "/org/freedesktop/ModemManager1/SIM/0"


def configure_info_proxies(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.side_effect = [fake_modem_proxy, fake_modem_proxy, fake_sim_proxy]


async def test_get_modem_info_returns_fully_populated_modem_info(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    info = await client.get_modem_info()

    assert info == ModemInfo(
        object_path=MODEM_PATH,
        manufacturer="Quectel",
        model="EC25-EUX",
        equipment_id="867698040000001",
        device="ttyUSB2",
        state="registered",
        registration=RegistrationState.ROAMING,
        signal=info.signal,
        sim_imsi="250010123456789",
        sim_operator_name="MTS RUS",
        sim_operator_id="25001",
    )


async def test_get_modem_info_handles_missing_sim_properties_gracefully(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    fake_sim_proxy.sim.get_imsi.side_effect = DBusError(
        "org.freedesktop.DBus.Error.UnknownProperty",
        "missing Imsi",
    )
    fake_sim_proxy.sim.get_operator_name.side_effect = DBusError(
        "org.freedesktop.DBus.Error.UnknownProperty",
        "missing OperatorName",
    )
    fake_sim_proxy.sim.get_operator_identifier.side_effect = DBusError(
        "org.freedesktop.DBus.Error.UnknownProperty",
        "missing OperatorIdentifier",
    )
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    info = await client.get_modem_info()

    assert info.sim_imsi is None
    assert info.sim_operator_name is None
    assert info.sim_operator_id is None


async def test_get_modem_info_propagates_optional_sim_property_transport_failures(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    error = DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "ModemManager restarted")
    fake_sim_proxy.sim.get_imsi.side_effect = error
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match="failed to read optional modem property Imsi",
    ) as exc:
        await client.get_modem_info()

    assert exc.value.__cause__ is error


@pytest.mark.parametrize("missing_sim_path", ["/", ""])
async def test_get_modem_info_handles_missing_sim_path_gracefully(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    fake_sim_proxy: MagicMock,
    missing_sim_path: str,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    fake_modem_props.get_sim.return_value = missing_sim_path
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    info = await client.get_modem_info()

    assert info.sim_imsi is None
    assert info.sim_operator_name is None
    assert info.sim_operator_id is None
    fake_sim_proxy.get_interface.assert_not_called()


async def test_get_modem_info_auto_calls_find_modem_when_path_is_not_cached(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    client = ModemManagerClient()
    client._bus = fake_bus

    async def fake_find_modem() -> str:
        client._modem_path = MODEM_PATH
        return MODEM_PATH

    client.find_modem = AsyncMock(side_effect=fake_find_modem)

    info = await client.get_modem_info()

    client.find_modem.assert_awaited_once_with()
    assert info.object_path == MODEM_PATH


async def test_get_modem_info_builds_signal_quality_from_dbus_tuple(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    fake_modem_props.get_signal_quality.return_value = (42, False)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    info = await client.get_modem_info()

    assert info.signal is not None
    assert info.signal.percent == 42
    assert info.signal.recent is False


@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        (-1, "failed"),
        (0, "unknown"),
        (1, "initializing"),
        (2, "locked"),
        (3, "disabled"),
        (4, "disabling"),
        (5, "enabling"),
        (6, "enabled"),
        (7, "searching"),
        (8, "registered"),
        (9, "disconnecting"),
        (10, "connecting"),
        (11, "connected"),
        (999, "unknown"),
        ("registered", "registered"),
    ],
)
async def test_get_modem_info_decodes_modem_state(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    fake_sim_proxy: MagicMock,
    raw_state: int | str,
    expected: str,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    fake_modem_props.get_state.return_value = raw_state
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    info = await client.get_modem_info()

    assert info.state == expected


async def test_get_modem_info_wraps_required_property_failures(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    fake_modem_props.get_manufacturer.side_effect = DBusError(
        "org.freedesktop.DBus.Error.Failed",
        "read failed",
    )
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(ModemError, match="failed to read required modem property Manufacturer"):
        await client.get_modem_info()


async def test_get_modem_info_wraps_modem_lookup_failures(
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
        await client.get_modem_info()

    assert exc.value.__cause__ is error


async def test_get_modem_info_wraps_modem_interface_lookup_failures(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    error = DBusError("org.freedesktop.DBus.Error.Failed", "lookup failed")

    def get_interface(interface_name: str) -> object:
        if interface_name == "org.freedesktop.ModemManager1.Modem.Modem3gpp":
            raise error
        return fake_modem_props

    fake_modem_proxy.get_interface.side_effect = get_interface
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match="failed to query modem object interface",
    ) as exc:
        await client.get_modem_info()

    assert exc.value.__cause__ is error


async def test_get_modem_info_wraps_sim_lookup_failures(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.UnknownObject", "SIM vanished")
    fake_bus.introspect.side_effect = [object(), object(), error]
    configure_info_proxies(fake_bus, fake_modem_proxy, fake_sim_proxy)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query SIM object {SIM_PATH}",
    ) as exc:
        await client.get_modem_info()

    assert exc.value.__cause__ is error
