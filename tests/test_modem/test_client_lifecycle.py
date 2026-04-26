from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, ModemNotFound

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"


async def test_connect_happy_path(
    fake_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_bus = MagicMock(return_value=fake_bus)
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", message_bus)
    client = ModemManagerClient()

    await client.connect()

    message_bus.assert_called_once()
    fake_bus.connect.assert_awaited_once()


async def test_connect_failure_raises_modem_manager_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = OSError("system bus unavailable")
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", MagicMock(side_effect=error))
    client = ModemManagerClient()

    with pytest.raises(ModemManagerUnavailable, match="failed to connect to system D-Bus") as exc:
        await client.connect()

    assert exc.value.__cause__ is error


async def test_disconnect_after_connect_succeeds(
    fake_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", MagicMock(return_value=fake_bus))
    client = ModemManagerClient()

    await client.connect()
    await client.disconnect()

    fake_bus.disconnect.assert_called_once_with()


async def test_disconnect_when_never_connected_is_noop() -> None:
    client = ModemManagerClient()

    await client.disconnect()

    assert client._bus is None


async def test_double_connect_creates_only_one_bus(
    fake_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_bus = MagicMock(return_value=fake_bus)
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", message_bus)
    client = ModemManagerClient()

    await client.connect()
    await client.connect()

    message_bus.assert_called_once()
    fake_bus.connect.assert_awaited_once()


async def test_connect_after_dropped_bus_clears_cached_modem_path(
    fake_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_bus = MagicMock()
    stale_bus.connected = False
    message_bus = MagicMock(return_value=fake_bus)
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", message_bus)
    client = ModemManagerClient()
    client._bus = stale_bus
    client._modem_path = "/org/freedesktop/ModemManager1/Modem/9"

    await client.connect()

    assert client._bus is fake_bus
    assert client._modem_path is None
    message_bus.assert_called_once()
    fake_bus.connect.assert_awaited_once()


async def test_connect_after_dropped_bus_resubscribes_added_watchers(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_bus = MagicMock()
    stale_bus.connected = False
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.get_proxy_object.side_effect = [object_manager_proxy, fake_messaging_proxy]
    message_bus = MagicMock(return_value=fake_bus)
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", message_bus)
    client = ModemManagerClient()
    client._bus = stale_bus

    async def callback(_sms_path: str) -> None:
        return None

    client._added_callbacks[client._callback_key(callback)] = callback
    await client.connect()

    assert client._bus is fake_bus
    assert client._modem_path == MODEM_PATH
    fake_messaging_proxy.messaging.on_added.assert_called_once()


async def test_connect_retries_watcher_resubscription_after_failed_reconnect(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_bus = MagicMock()
    stale_bus.connected = False
    empty_object_manager = MagicMock()
    empty_object_manager.call_get_managed_objects = AsyncMock(return_value={})
    empty_object_manager_proxy = MagicMock()
    empty_object_manager_proxy.get_interface.return_value = empty_object_manager
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.get_proxy_object.side_effect = [
        empty_object_manager_proxy,
        object_manager_proxy,
        fake_messaging_proxy,
    ]
    message_bus = MagicMock(return_value=fake_bus)
    monkeypatch.setattr("sms_gateway_v2.modem.client.MessageBus", message_bus)
    client = ModemManagerClient()
    client._bus = stale_bus

    async def callback(_sms_path: str) -> None:
        return None

    client._added_callbacks[client._callback_key(callback)] = callback
    with pytest.raises(ModemNotFound, match="no ModemManager modem object found"):
        await client.connect()

    await client.connect()

    assert client._bus is fake_bus
    assert client._modem_path == MODEM_PATH
    assert client._added_watch_resubscribe_required is False
    message_bus.assert_called_once()
    fake_messaging_proxy.messaging.on_added.assert_called_once()
