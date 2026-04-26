from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, ModemNotFound

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"


def configure_object_manager(
    fake_bus: MagicMock,
    managed_objects: dict[str, dict[str, object]],
) -> MagicMock:
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(return_value=managed_objects)
    proxy = MagicMock()
    proxy.get_interface.return_value = object_manager
    fake_bus.get_proxy_object.return_value = proxy
    return object_manager


async def test_find_modem_returns_first_path_when_one_modem_is_exposed(
    fake_bus: MagicMock,
) -> None:
    configure_object_manager(
        fake_bus,
        {
            "/org/freedesktop/ModemManager1/Modem/0": {
                MODEM_INTERFACE: object(),
            }
        },
    )
    client = ModemManagerClient()
    client._bus = fake_bus

    modem_path = await client.find_modem()

    assert modem_path == "/org/freedesktop/ModemManager1/Modem/0"
    assert client._modem_path == modem_path


async def test_find_modem_picks_object_with_modem_interface(
    fake_bus: MagicMock,
) -> None:
    configure_object_manager(
        fake_bus,
        {
            "/org/freedesktop/ModemManager1/SIM/0": {
                "org.freedesktop.ModemManager1.Sim": object(),
            },
            "/org/freedesktop/ModemManager1/Modem/1": {
                MODEM_INTERFACE: object(),
            },
        },
    )
    client = ModemManagerClient()
    client._bus = fake_bus

    assert await client.find_modem() == "/org/freedesktop/ModemManager1/Modem/1"


async def test_find_modem_raises_modem_not_found_when_no_modem_interface_exists(
    fake_bus: MagicMock,
) -> None:
    configure_object_manager(
        fake_bus,
        {
            "/org/freedesktop/ModemManager1/SIM/0": {
                "org.freedesktop.ModemManager1.Sim": object(),
            }
        },
    )
    client = ModemManagerClient()
    client._bus = fake_bus

    with pytest.raises(ModemNotFound, match="no ModemManager modem object found"):
        await client.find_modem()


async def test_find_modem_without_prior_connect_raises_modem_manager_unavailable() -> None:
    client = ModemManagerClient()

    with pytest.raises(ModemManagerUnavailable, match="not connected to system D-Bus"):
        await client.find_modem()
