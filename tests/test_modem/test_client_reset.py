from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable
from tests.test_modem.factories import make_fake_messaging_proxy

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REDISCOVERED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"


@pytest.fixture
def fake_reset_proxy() -> MagicMock:
    modem = MagicMock()
    modem.call_reset = AsyncMock(return_value=None)

    proxy = MagicMock()
    proxy.modem = modem
    proxy.get_interface.return_value = modem
    return proxy


async def test_reset_calls_modem_reset_on_cached_modem_path(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.reset()

    fake_reset_proxy.get_interface.assert_called_with(MODEM_INTERFACE)
    fake_reset_proxy.modem.call_reset.assert_awaited_once_with()


async def test_reset_clears_cached_modem_path_so_next_operation_rediscovers(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.reset()

    assert client._modem_path is None


async def test_reset_clears_watch_keys_and_marks_resubscribe_required(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert client._added_watch_keys == set()
    assert client._added_watch_resubscribe_required is True


async def test_reset_does_not_mark_resubscribe_when_no_callbacks_registered(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.reset()

    assert client._added_watch_resubscribe_required is False


async def test_reset_wraps_dbus_errors_as_modem_manager_unavailable(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.NoReply", "modem timed out")
    fake_reset_proxy.modem.call_reset = AsyncMock(side_effect=error)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to reset modem at {MODEM_PATH}",
    ) as exc:
        await client.reset()

    assert exc.value.__cause__ is error


async def test_reset_resubscribes_added_watchers_on_next_operation(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        object_manager_proxy,
        refreshed_messaging_proxy,
        fake_modem_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()
    await client.get_signal_quality()

    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._modem_path == REDISCOVERED_MODEM_PATH
    assert (REDISCOVERED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_reset_retries_resubscribe_when_first_attempt_fails(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    messaging_unavailable = DBusError(
        "org.freedesktop.DBus.Error.NoReply",
        "Messaging interface unavailable",
    )
    fake_bus.introspect.side_effect = [
        object(),
        object(),
        messaging_unavailable,
        object(),
        object(),
    ]
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        object_manager_proxy,
        refreshed_messaging_proxy,
        fake_modem_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    with pytest.raises(ModemManagerUnavailable):
        await client.get_signal_quality()

    assert client._modem_path == REDISCOVERED_MODEM_PATH
    assert client._added_watch_resubscribe_required is True
    assert (REDISCOVERED_MODEM_PATH, callback_key) not in client._added_watch_keys

    await client.get_signal_quality()

    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (REDISCOVERED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False
