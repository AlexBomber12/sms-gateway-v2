from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError
from dbus_fast.errors import InterfaceNotFoundError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, ModemNotFound

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
OTHER_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/2"
MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
UNKNOWN_OBJECT_ERROR = "org.freedesktop.DBus.Error.UnknownObject"


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


async def test_get_signal_quality_recovers_from_interface_not_found_on_cached_path(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
    fake_modem_props: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    fake_modem_props.get_signal_quality.return_value = (89, True)
    fake_bus.get_proxy_object.side_effect = [stale_proxy, fake_modem_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)
    client._resubscribe_added_watchers = AsyncMock()

    signal = await client.get_signal_quality()

    assert signal.percent == 89
    assert signal.recent is True
    assert client._modem_path == REFRESHED_MODEM_PATH
    client.find_modem.assert_awaited_once_with()
    client._resubscribe_added_watchers.assert_awaited_once_with(REFRESHED_MODEM_PATH)


async def test_get_signal_quality_does_not_recover_when_path_is_not_cached(
    fake_bus: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client._get_proxy_object = AsyncMock(return_value=(OTHER_MODEM_PATH, stale_proxy))
    client.find_modem = AsyncMock(return_value=REFRESHED_MODEM_PATH)

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query modem object interface {MODEM_INTERFACE} on {OTHER_MODEM_PATH}",
    ):
        await client.get_signal_quality()

    client.find_modem.assert_not_awaited()


async def test_get_signal_quality_propagates_when_find_modem_fails(
    fake_bus: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    fake_bus.get_proxy_object.return_value = stale_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client.find_modem = AsyncMock(side_effect=ModemNotFound("no modem"))

    with pytest.raises(ModemNotFound, match="no modem"):
        await client.get_signal_quality()

    client.find_modem.assert_awaited_once_with()


async def test_get_signal_quality_propagates_other_dbus_errors_unchanged(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.Failed", "temporary failure")
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = error
    fake_bus.get_proxy_object.return_value = stale_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client.find_modem = AsyncMock(return_value=REFRESHED_MODEM_PATH)

    with pytest.raises(ModemManagerUnavailable) as exc:
        await client.get_signal_quality()

    assert exc.value.__cause__ is error
    assert client._is_stale_modem_path_error(error) is False
    client.find_modem.assert_not_awaited()


async def test_unknown_object_error_path_still_triggers_refresh(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={REFRESHED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.introspect.side_effect = [
        DBusError(UNKNOWN_OBJECT_ERROR, "stale modem path"),
        object(),
        object(),
    ]
    fake_bus.get_proxy_object.side_effect = [object_manager_proxy, fake_modem_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    signal = await client.get_signal_quality()

    assert signal.percent == 76
    assert signal.recent is True
    assert client._modem_path == REFRESHED_MODEM_PATH


async def test_proxy_object_refresh_still_resubscribes_watchers_by_default(
    fake_bus: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={REFRESHED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.introspect.side_effect = [
        DBusError(UNKNOWN_OBJECT_ERROR, "stale modem path"),
        object(),
        object(),
    ]
    fake_bus.get_proxy_object.side_effect = [object_manager_proxy, fake_modem_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client._resubscribe_added_watchers = AsyncMock()

    modem_path, proxy = await client._get_proxy_object(
        "modem object",
        MODEM_PATH,
        refresh_cached_modem=True,
    )

    assert modem_path == REFRESHED_MODEM_PATH
    assert proxy is fake_modem_proxy
    client._resubscribe_added_watchers.assert_awaited_once_with(REFRESHED_MODEM_PATH)
