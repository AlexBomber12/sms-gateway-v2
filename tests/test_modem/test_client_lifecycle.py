from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable


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
