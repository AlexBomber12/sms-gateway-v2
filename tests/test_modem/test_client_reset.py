from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

import sms_gateway_v2.modem.client as client_module
from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable, ModemNotFound
from tests.test_modem.factories import make_fake_messaging_proxy

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REDISCOVERED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
SMS_PATH = "/org/freedesktop/ModemManager1/SMS/1"


@pytest.fixture
def fake_reset_proxy() -> MagicMock:
    modem = MagicMock()
    modem.call_reset = AsyncMock(return_value=None)

    proxy = MagicMock()
    proxy.modem = modem
    proxy.get_interface.return_value = modem
    return proxy


def make_object_manager_proxy(managed_objects: dict[str, dict[str, object]]) -> MagicMock:
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(return_value=managed_objects)
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    return object_manager_proxy


def make_fake_time(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    elapsed_seconds = [0.0]

    def monotonic() -> float:
        return elapsed_seconds[0]

    async def sleep(seconds: float) -> None:
        elapsed_seconds[0] += seconds

    monkeypatch.setattr(client_module.time, "monotonic", monotonic)
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    return elapsed_seconds


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


async def test_reset_unsubscribes_existing_watchers_and_marks_resubscribe_required(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_messaging_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_fake_time(monkeypatch)
    object_manager_proxy = make_object_manager_proxy({})
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        fake_reset_proxy,
        object_manager_proxy,
        object_manager_proxy,
        object_manager_proxy,
    ]
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    await client.watch_added(callback)
    callback_key = client._callback_key(callback)
    registered_handler = fake_messaging_proxy.messaging.added_handler

    await client.reset()

    fake_messaging_proxy.messaging.off_added.assert_called_once_with(registered_handler)
    assert client._added_watch_keys == set()
    assert client._added_watch_handlers == {}
    assert client._added_watch_resubscribe_required is True
    assert callback_key in client._added_callbacks


async def test_reset_logs_when_unsubscribing_existing_watcher_fails(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_messaging_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_fake_time(monkeypatch)
    object_manager_proxy = make_object_manager_proxy({})
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        fake_reset_proxy,
        object_manager_proxy,
        object_manager_proxy,
        object_manager_proxy,
    ]
    fake_messaging_proxy.messaging.off_added = MagicMock(
        side_effect=DBusError("org.freedesktop.DBus.Error.NoReply", "off_added timed out")
    )
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    await client.watch_added(callback)

    await client.reset()

    captured_logger.info.assert_any_call(
        "added_watch_unsubscribe_failed",
        modem_path=MODEM_PATH,
        error="off_added timed out",
    )
    assert client._added_watch_keys == set()
    assert client._added_watch_handlers == {}
    assert client._added_watch_resubscribe_required is True


async def test_reset_does_not_redeliver_through_old_handler_when_path_unchanged(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_messaging_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_fake_time(monkeypatch)
    missing_proxy = make_object_manager_proxy({})
    object_manager_proxy = make_object_manager_proxy({MODEM_PATH: {MODEM_INTERFACE: object()}})
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        fake_reset_proxy,
        missing_proxy,
        object_manager_proxy,
        fake_messaging_proxy,
        fake_modem_proxy,
    ]
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    received_paths: list[str] = []

    async def callback(sms_path: str) -> None:
        received_paths.append(sms_path)

    await client.watch_added(callback)
    first_handler = fake_messaging_proxy.messaging.added_handler
    await client.reset()
    await client.get_signal_quality()
    second_handler = fake_messaging_proxy.messaging.added_handler

    assert first_handler is not None
    assert second_handler is not None
    assert second_handler is not first_handler
    fake_messaging_proxy.messaging.off_added.assert_called_once_with(first_handler)
    assert fake_messaging_proxy.messaging.on_added.call_count == 2
    assert client._added_watch_keys == {
        (MODEM_PATH, client._callback_key(callback)),
    }


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
    object_manager_proxy = make_object_manager_proxy(
        {REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
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

    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._modem_path == REDISCOVERED_MODEM_PATH
    assert (REDISCOVERED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_reset_retries_resubscribe_when_first_attempt_fails(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    fake_modem_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_fake_time(monkeypatch)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    object_manager_proxy = make_object_manager_proxy(
        {REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}}
    )
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
        object_manager_proxy,
        refreshed_messaging_proxy,
    ]
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert client._modem_path == REDISCOVERED_MODEM_PATH
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert (REDISCOVERED_MODEM_PATH, callback_key) in client._added_watch_keys
    assert client._added_watch_resubscribe_required is False


async def test_reset_waits_for_modem_reappear_and_resubscribes(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    missing_proxy = make_object_manager_proxy({})
    found_proxy = make_object_manager_proxy({REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}})
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        missing_proxy,
        missing_proxy,
        found_proxy,
        refreshed_messaging_proxy,
    ]
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient(reset_reappear_timeout_seconds=10.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(4.0)
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._added_watch_keys == {(REDISCOVERED_MODEM_PATH, callback_key)}
    captured_logger.info.assert_any_call(
        "modem_reset_reappeared",
        modem_path=REDISCOVERED_MODEM_PATH,
        wait_seconds=pytest.approx(4.0),
    )


async def test_reset_ignores_reset_modem_path_until_it_disappears(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    reset_path_proxy = make_object_manager_proxy({MODEM_PATH: {MODEM_INTERFACE: object()}})
    found_proxy = make_object_manager_proxy({REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()}})
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        reset_path_proxy,
        found_proxy,
        refreshed_messaging_proxy,
    ]
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient(reset_reappear_timeout_seconds=10.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(2.0)
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._modem_path == REDISCOVERED_MODEM_PATH
    assert client._added_watch_keys == {(REDISCOVERED_MODEM_PATH, callback_key)}
    captured_logger.info.assert_any_call(
        "modem_reset_reappeared",
        modem_path=REDISCOVERED_MODEM_PATH,
        wait_seconds=pytest.approx(2.0),
    )


async def test_reset_searches_past_reset_path_when_fresh_modem_is_already_exposed(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    refreshed_messaging_proxy = make_fake_messaging_proxy()
    both_paths_proxy = make_object_manager_proxy(
        {
            MODEM_PATH: {MODEM_INTERFACE: object()},
            REDISCOVERED_MODEM_PATH: {MODEM_INTERFACE: object()},
        }
    )
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        both_paths_proxy,
        refreshed_messaging_proxy,
    ]
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient(reset_reappear_timeout_seconds=10.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(0.0)
    refreshed_messaging_proxy.messaging.on_added.assert_called_once()
    assert client._modem_path == REDISCOVERED_MODEM_PATH
    assert client._added_watch_keys == {(REDISCOVERED_MODEM_PATH, callback_key)}
    captured_logger.info.assert_any_call(
        "modem_reset_reappeared",
        modem_path=REDISCOVERED_MODEM_PATH,
        wait_seconds=pytest.approx(0.0),
    )


async def test_reset_returns_on_reappear_timeout_without_raising(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    object_manager_proxy = make_object_manager_proxy({})
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        object_manager_proxy,
        object_manager_proxy,
        object_manager_proxy,
    ]
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(5.0)
    assert client._modem_path is None
    assert client._added_watch_resubscribe_required is True
    captured_logger.warning.assert_any_call(
        "modem_reset_reappear_timeout",
        wait_seconds=pytest.approx(5.0),
    )


async def test_reset_skips_wait_when_no_added_callbacks(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    find_modem = AsyncMock(side_effect=ModemNotFound("missing"))
    monkeypatch.setattr(client, "find_modem", find_modem)

    await client.reset()

    find_modem.assert_not_awaited()
    assert elapsed_seconds[0] < 1.0


async def test_reset_reappear_wait_bounded_by_setting(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    object_manager_proxy = make_object_manager_proxy({})
    fake_bus.get_proxy_object.side_effect = [
        fake_reset_proxy,
        object_manager_proxy,
        object_manager_proxy,
        object_manager_proxy,
        object_manager_proxy,
    ]
    client = ModemManagerClient(reset_reappear_timeout_seconds=6.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(6.0)


async def test_reset_reappear_wait_stops_when_poll_exhausts_timeout(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    async def list_modem_paths() -> list[str]:
        elapsed_seconds[0] = 5.0
        return []

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "_list_modem_paths", list_modem_paths)

    await client.reset()

    sleep.assert_not_awaited()
    assert elapsed_seconds[0] == pytest.approx(5.0)


async def test_reset_reappear_wait_sleeps_when_modem_not_found_before_timeout(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=2.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    async def list_modem_paths() -> list[str]:
        raise ModemNotFound("missing")

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))
    monkeypatch.setattr(client, "_list_modem_paths", list_modem_paths)

    await client.reset()

    assert elapsed_seconds[0] == pytest.approx(2.0)


async def test_reset_reappear_wait_stops_when_modem_not_found_exhausts_timeout(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    async def list_modem_paths() -> list[str]:
        elapsed_seconds[0] = 5.0
        raise ModemNotFound("missing")

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "_list_modem_paths", list_modem_paths)

    await client.reset()

    sleep.assert_not_awaited()
    assert elapsed_seconds[0] == pytest.approx(5.0)


async def test_reset_reappear_wait_stops_when_reset_path_poll_exhausts_timeout(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    async def list_modem_paths() -> list[str]:
        elapsed_seconds[0] = 5.0
        return [MODEM_PATH]

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "_list_modem_paths", list_modem_paths)

    await client.reset()

    sleep.assert_not_awaited()
    assert elapsed_seconds[0] == pytest.approx(5.0)
    assert client._modem_path is None
    assert client._added_watch_resubscribe_required is True


async def test_reset_reappear_wait_stops_when_modem_error_exhausts_timeout(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed_seconds = make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    client = ModemManagerClient(reset_reappear_timeout_seconds=5.0)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def callback(_sms_path: str) -> None:
        return None

    async def list_modem_paths() -> list[str]:
        elapsed_seconds[0] = 5.0
        raise ModemManagerUnavailable("query failed")

    callback_key = client._callback_key(callback)
    client._added_callbacks[callback_key] = callback
    client._added_watch_keys.add((MODEM_PATH, callback_key))
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "_list_modem_paths", list_modem_paths)

    await client.reset()

    sleep.assert_not_awaited()
    assert elapsed_seconds[0] == pytest.approx(5.0)


async def test_reset_still_emits_modem_reset_called(
    fake_bus: MagicMock,
    fake_reset_proxy: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_fake_time(monkeypatch)
    fake_bus.get_proxy_object.return_value = fake_reset_proxy
    captured_logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", captured_logger)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.reset()

    captured_logger.info.assert_any_call(
        "modem_reset_called",
        duration_seconds=0.0,
        modem_path=MODEM_PATH,
    )
