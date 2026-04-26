from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from sms_gateway_v2.modem import ModemManagerClient

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
SMS_PATH = "/org/freedesktop/ModemManager1/SMS/1"


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

    fake_messaging_proxy.messaging.on_added.assert_called_once()
    assert received_paths == [SMS_PATH]


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
