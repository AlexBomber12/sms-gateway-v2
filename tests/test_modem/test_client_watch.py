from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError
from dbus_fast.errors import InterfaceNotFoundError

from sms_gateway_v2.modem import ModemManagerClient
from sms_gateway_v2.modem.exceptions import ModemManagerUnavailable, ModemNotFound
from tests.test_modem.factories import make_fake_messaging_proxy

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
SMS_PATH = "/org/freedesktop/ModemManager1/SMS/1"
UNKNOWN_OBJECT_ERROR = "org.freedesktop.DBus.Error.UnknownObject"


class WatchReceiver:
    def __init__(self) -> None:
        self.received_paths: list[str] = []
        self.signal_received = asyncio.Event()

    async def callback(self, sms_path: str) -> None:
        self.received_paths.append(sms_path)
        self.signal_received.set()


async def test_watch_added_invokes_callback_when_added_signal_is_emitted(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []
    signal_received = asyncio.Event()

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)
        signal_received.set()

    await client.watch_added(callback)
    fake_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    assert received_paths == [SMS_PATH]


async def test_watch_added_deduplicates_repeated_bound_method_registration(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    receiver = WatchReceiver()

    await client.watch_added(receiver.callback)
    await client.watch_added(receiver.callback)
    fake_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(receiver.signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    assert receiver.received_paths == [SMS_PATH]


async def test_watch_added_rebinds_existing_watchers_when_subscription_refreshes_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        stale_proxy,
        refreshed_messaging_proxy,
        refreshed_messaging_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    first_callback = AsyncMock()
    second_callback = AsyncMock()

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    await client.watch_added(first_callback)
    await client.watch_added(second_callback)

    first_key = client._callback_key(first_callback)
    second_key = client._callback_key(second_callback)
    assert (MODEM_PATH, first_key) not in client._added_watch_keys
    assert (REFRESHED_MODEM_PATH, first_key) in client._added_watch_keys
    assert (REFRESHED_MODEM_PATH, second_key) in client._added_watch_keys
    fake_messaging_proxy.messaging.off_added.assert_called_once()
    assert refreshed_messaging_proxy.messaging.on_added.call_count == 2


async def test_watch_added_ignores_non_received_added_signal(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)

    await client.watch_added(callback)
    fake_messaging_proxy.messaging.added_handler(SMS_PATH, False)
    await asyncio.sleep(0)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    assert received_paths == []


async def test_watch_added_logs_callback_failure(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", logger)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        raise RuntimeError("callback failed")

    await client.watch_added(callback)
    fake_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await wait_for_watch_tasks(client)

    logger.exception.assert_called_once_with(
        "message_added_callback_failed",
        modem_path=MODEM_PATH,
        sms_path=SMS_PATH,
        error="callback failed",
    )


async def test_watch_added_resubscribes_after_cached_modem_path_refresh(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={REFRESHED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.introspect.side_effect = [
        object(),
        DBusError(UNKNOWN_OBJECT_ERROR, "stale modem path"),
        object(),
        object(),
        object(),
    ]
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        object_manager_proxy,
        fake_modem_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []
    signal_received = asyncio.Event()

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)
        signal_received.set()

    await client.watch_added(callback)
    await client.get_signal_quality()
    refreshed_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._modem_path == REFRESHED_MODEM_PATH
    assert received_paths == [SMS_PATH]


async def test_watch_added_rebinds_after_deferred_same_path_proxy_refresh(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.introspect.side_effect = [
        object(),
        DBusError(UNKNOWN_OBJECT_ERROR, "stale modem path"),
        object(),
        object(),
        object(),
    ]
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        object_manager_proxy,
        fake_modem_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []
    signal_received = asyncio.Event()

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)
        signal_received.set()

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    await client.get_signal_quality()
    refreshed_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.off_added.assert_called_once()
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False
    assert received_paths == [SMS_PATH]


async def test_message_added_subscription_follows_modem_path_after_refresh(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    stale_modem_proxy = MagicMock()
    stale_modem_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        stale_modem_proxy,
        fake_modem_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []
    signal_received = asyncio.Event()

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)
        signal_received.set()

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    await client.get_signal_quality()
    refreshed_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (REFRESHED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert (MODEM_PATH, callback_key) not in client._added_watch_keys
    assert received_paths == [SMS_PATH]


async def test_message_added_subscription_rebinds_after_same_path_refresh(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    stale_modem_proxy = MagicMock()
    stale_modem_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        stale_modem_proxy,
        fake_modem_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []
    signal_received = asyncio.Event()

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)
        signal_received.set()

    async def refresh_modem() -> str:
        client._modem_path = MODEM_PATH
        return MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    await client.get_signal_quality()
    refreshed_messaging_proxy.messaging.added_handler(SMS_PATH, True)
    await asyncio.wait_for(signal_received.wait(), timeout=1)
    await wait_for_watch_tasks(client)

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    fake_messaging_proxy.messaging.off_added.assert_called_once()
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False
    assert received_paths == [SMS_PATH]


async def test_message_added_subscription_rebinds_after_partial_refresh_failure(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    stale_modem_proxy = MagicMock()
    stale_modem_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    partial_refreshed_proxy = MagicMock()
    partial_refreshed_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        stale_modem_proxy,
        partial_refreshed_proxy,
        refreshed_messaging_proxy,
        fake_modem_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    callback = AsyncMock()

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)

    with pytest.raises(ModemManagerUnavailable):
        await client.get_signal_quality()

    fake_messaging_proxy.messaging.off_added.assert_called_once()
    assert client._added_watch_resubscribe_required is True
    assert client._added_watch_keys == set()

    await client.get_signal_quality()

    client.find_modem.assert_awaited_once_with()
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (REFRESHED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_message_added_subscription_rebinds_after_deferred_find_modem_failure(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.introspect.side_effect = [
        object(),
        DBusError(UNKNOWN_OBJECT_ERROR, "stale modem path"),
    ]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    callback = AsyncMock()

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    client.find_modem = AsyncMock(side_effect=ModemNotFound("no modem"))

    with pytest.raises(ModemNotFound, match="no modem"):
        await client.get_signal_quality()

    fake_messaging_proxy.messaging.off_added.assert_called_once()
    assert client._modem_path is None
    assert client._added_watch_resubscribe_required is True
    assert client._added_watch_keys == set()

    async def refresh_modem() -> str:
        client._modem_path = MODEM_PATH
        return MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)
    fake_bus.introspect.side_effect = [object(), object()]
    fake_bus.get_proxy_object.side_effect = [
        refreshed_messaging_proxy,
        fake_modem_proxy,
    ]

    signal = await client.get_signal_quality()

    assert signal.percent == 76
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_message_added_subscription_rebinds_after_find_modem_failure(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
) -> None:
    stale_modem_proxy = MagicMock()
    stale_modem_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        stale_modem_proxy,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    callback = AsyncMock()

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    client.find_modem = AsyncMock(side_effect=ModemNotFound("no modem"))

    with pytest.raises(ModemNotFound, match="no modem"):
        await client.get_signal_quality()

    fake_messaging_proxy.messaging.off_added.assert_called_once()
    assert client._modem_path is None
    assert client._added_watch_resubscribe_required is True
    assert client._added_watch_keys == set()

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)
    fake_bus.get_proxy_object.side_effect = [
        refreshed_messaging_proxy,
        fake_modem_proxy,
    ]

    signal = await client.get_signal_quality()

    assert signal.percent == 76
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (REFRESHED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_watch_added_does_not_recursively_refresh_when_messaging_is_unavailable(
    fake_bus: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    refreshed_proxy = MagicMock()
    refreshed_proxy.get_interface.side_effect = InterfaceNotFoundError(MODEM_INTERFACE)
    fake_bus.get_proxy_object.side_effect = [stale_proxy, refreshed_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    callback = AsyncMock()
    client.find_modem = AsyncMock(side_effect=refresh_modem)

    with pytest.raises(ModemManagerUnavailable):
        await client.watch_added(callback)

    client.find_modem.assert_awaited_once_with()
    assert client._added_watch_keys == set()
    assert client._added_watch_handlers == {}


async def wait_for_watch_tasks(client: ModemManagerClient) -> None:
    for _ in range(10):
        await asyncio.sleep(0)
        if not client._watch_tasks:
            return
    pytest.fail("watch callback task did not complete")
