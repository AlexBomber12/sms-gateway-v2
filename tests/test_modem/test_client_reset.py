from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable

MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"


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
