from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import ModemError, ModemInfo, ModemManagerClient, RegistrationState

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
SIM_PATH = "/org/freedesktop/ModemManager1/SIM/0"


def configure_info_proxies(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_sim_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.side_effect = [fake_modem_proxy, fake_sim_proxy]


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
