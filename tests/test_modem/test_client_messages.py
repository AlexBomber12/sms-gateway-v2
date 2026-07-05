from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError
from dbus_fast.errors import InterfaceNotFoundError

from sms_gateway_v2.modem import MessageDeleteFailed, ModemManagerClient, ModemManagerUnavailable

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
SMS_PATH_1 = "/org/freedesktop/ModemManager1/SMS/1"
SMS_PATH_2 = "/org/freedesktop/ModemManager1/SMS/2"
SMS_PATH_10 = "/org/freedesktop/ModemManager1/SMS/10"
MESSAGING_INTERFACE = "org.freedesktop.ModemManager1.Modem.Messaging"
SMS_INTERFACE = "org.freedesktop.ModemManager1.Sms"


def make_sms_proxy(
    *,
    number: str,
    text: str,
    timestamp: str | None,
    pdu_type: int | str = 1,
) -> MagicMock:
    sms = MagicMock()
    sms.get_number = AsyncMock(return_value=number)
    sms.get_text = AsyncMock(return_value=text)
    sms.get_pdu_type = AsyncMock(return_value=pdu_type)
    if timestamp is None:
        sms.get_timestamp = AsyncMock(
            side_effect=DBusError(
                "org.freedesktop.DBus.Error.UnknownProperty",
                "missing Timestamp",
            )
        )
    else:
        sms.get_timestamp = AsyncMock(return_value=timestamp)

    proxy = MagicMock()
    proxy.sms = sms
    proxy.get_interface.return_value = sms
    return proxy


def make_properties_proxy() -> MagicMock:
    properties = MagicMock()
    properties.on_properties_changed = MagicMock()
    properties.off_properties_changed = MagicMock()

    proxy = MagicMock()
    proxy.properties = properties
    proxy.get_interface.return_value = properties
    return proxy


async def test_list_messages_returns_parsed_messages_ordered_by_timestamp(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    sms_later = make_sms_proxy(
        number="+15550000002",
        text="later",
        timestamp="2026-04-26T10:42:00+00:00",
    )
    sms_earlier = make_sms_proxy(
        number="+15550000001",
        text="earlier",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_2, SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms_later, sms_earlier]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert [message.object_path for message in messages] == [SMS_PATH_1, SMS_PATH_2]
    assert messages[0].number == "+15550000001"
    assert messages[0].text == "earlier"
    assert messages[0].pdu_type == "deliver"


async def test_list_messages_parses_modem_manager_timestamp_offset_without_colon(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T13:41:00+0300",
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert messages[0].timestamp is not None
    assert messages[0].timestamp.isoformat() == "2026-04-26T13:41:00+03:00"


async def test_list_messages_handles_missing_timestamp_by_ordering_by_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    sms_without_timestamp = make_sms_proxy(
        number="+15550000002",
        text="without timestamp",
        timestamp=None,
    )
    sms_with_timestamp = make_sms_proxy(
        number="+15550000001",
        text="with timestamp",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_2, SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        sms_without_timestamp,
        sms_with_timestamp,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert [message.object_path for message in messages] == [SMS_PATH_1, SMS_PATH_2]
    assert messages[1].timestamp is None


async def test_list_messages_preserves_timestamp_order_when_some_timestamps_are_missing(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    sms_without_timestamp = make_sms_proxy(
        number="+15550000010",
        text="without timestamp",
        timestamp=None,
    )
    sms_later = make_sms_proxy(
        number="+15550000002",
        text="later",
        timestamp="2026-04-26T10:42:00+00:00",
    )
    sms_earlier = make_sms_proxy(
        number="+15550000001",
        text="earlier",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [
        SMS_PATH_10,
        SMS_PATH_2,
        SMS_PATH_1,
    ]
    fake_bus.get_proxy_object.side_effect = [
        fake_messaging_proxy,
        sms_without_timestamp,
        sms_later,
        sms_earlier,
    ]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert [message.object_path for message in messages] == [SMS_PATH_1, SMS_PATH_2, SMS_PATH_10]


async def test_list_messages_sorts_missing_timestamps_by_numeric_path_suffix(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    sms_10 = make_sms_proxy(
        number="+15550000010",
        text="ten",
        timestamp=None,
    )
    sms_2 = make_sms_proxy(
        number="+15550000002",
        text="two",
        timestamp=None,
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_10, SMS_PATH_2]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms_10, sms_2]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert [message.object_path for message in messages] == [SMS_PATH_2, SMS_PATH_10]


@pytest.mark.parametrize("timestamp", ["", "not-a-timestamp"])
async def test_list_messages_tolerates_blank_or_invalid_timestamp(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    timestamp: str,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp=timestamp,
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert messages[0].timestamp is None


async def test_list_messages_propagates_timestamp_transport_failures(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "ModemManager restarted")
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    sms.sms.get_timestamp.side_effect = error
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match="failed to read optional modem property Timestamp",
    ) as exc:
        await client.list_messages()

    assert exc.value.__cause__ is error


async def test_list_messages_refreshes_stale_cached_modem_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    stale_error = DBusError("org.freedesktop.DBus.Error.UnknownObject", "modem vanished")
    object_manager = MagicMock()
    object_manager.call_get_managed_objects = AsyncMock(
        return_value={
            REFRESHED_MODEM_PATH: {
                "org.freedesktop.ModemManager1.Modem": {},
            },
        }
    )
    object_manager_proxy = MagicMock()
    object_manager_proxy.get_interface.return_value = object_manager
    fake_bus.introspect.side_effect = [stale_error, object(), object()]
    fake_bus.get_proxy_object.side_effect = [object_manager_proxy, fake_messaging_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert messages == []
    assert client._modem_path == REFRESHED_MODEM_PATH
    fake_messaging_proxy.messaging.get_messages.assert_awaited_once_with()


async def test_list_messages_recovers_from_interface_not_found_on_cached_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MESSAGING_INTERFACE)
    fake_bus.get_proxy_object.side_effect = [stale_proxy, fake_messaging_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    messages = await client.list_messages()

    assert messages == []
    assert client._modem_path == REFRESHED_MODEM_PATH
    client.find_modem.assert_awaited_once_with()
    fake_messaging_proxy.messaging.get_messages.assert_awaited_once_with()


async def test_list_messages_does_not_recover_when_interface_error_is_not_stale(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.Failed", "lookup failed")
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = error
    fake_bus.get_proxy_object.return_value = stale_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client.find_modem = AsyncMock(return_value=REFRESHED_MODEM_PATH)

    with pytest.raises(ModemManagerUnavailable) as exc:
        await client.list_messages()

    assert exc.value.__cause__ is error
    client.find_modem.assert_not_awaited()


async def test_list_messages_does_not_recover_when_path_is_not_cached(
    fake_bus: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MESSAGING_INTERFACE)
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH
    client._get_proxy_object = AsyncMock(return_value=(REFRESHED_MODEM_PATH, stale_proxy))
    client.find_modem = AsyncMock(return_value=REFRESHED_MODEM_PATH)

    with pytest.raises(ModemManagerUnavailable):
        await client.list_messages()

    client.find_modem.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw_pdu_type", "expected"),
    [
        (1, "deliver"),
        (32, "cdma-deliver"),
        ("deliver", "deliver"),
    ],
)
async def test_list_messages_decodes_inbound_pdu_type(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    raw_pdu_type: int | str,
    expected: str,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
        pdu_type=raw_pdu_type,
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert messages[0].pdu_type == expected


@pytest.mark.parametrize(
    "raw_pdu_type",
    [
        0,
        2,
        3,
        33,
        34,
        35,
        36,
        37,
        999,
        "submit",
    ],
)
async def test_list_messages_filters_non_inbound_pdu_type(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
    raw_pdu_type: int | str,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
        pdu_type=raw_pdu_type,
    )
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.get_proxy_object.side_effect = [fake_messaging_proxy, sms]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    messages = await client.list_messages()

    assert messages == []
    sms.sms.get_number.assert_not_awaited()
    sms.sms.get_text.assert_not_awaited()
    sms.sms.get_timestamp.assert_not_awaited()


async def test_read_message_returns_text_immediately_if_already_populated(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    fake_bus.get_proxy_object.return_value = sms
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    message = await client.read_message(SMS_PATH_1)

    assert message is not None
    assert message.object_path == SMS_PATH_1
    assert message.text == "message"
    assert sms.sms.get_text.await_count == 1
    assert fake_bus.get_proxy_object.call_count == 2


async def test_read_message_returns_populated_text_when_properties_lookup_fails(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    properties_error = DBusError(
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "ModemManager restarted",
    )
    fake_bus.introspect.side_effect = [object(), properties_error]
    fake_bus.get_proxy_object.return_value = sms
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    message = await client.read_message(SMS_PATH_1)

    assert message is not None
    assert message.object_path == SMS_PATH_1
    assert message.text == "message"
    assert sms.sms.get_text.await_count == 1
    assert fake_bus.get_proxy_object.call_count == 1


async def test_read_message_waits_for_text_then_returns(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    sms.sms.get_text.side_effect = ["", "populated"]
    properties = make_properties_proxy()

    def on_properties_changed(callback: object) -> None:
        assert callable(callback)
        asyncio.get_running_loop().call_soon(callback, SMS_INTERFACE, {"Text": object()}, [])

    properties.properties.on_properties_changed.side_effect = on_properties_changed
    fake_bus.get_proxy_object.side_effect = [sms, properties]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    started_at = time.monotonic()
    message = await client.read_message(SMS_PATH_1)
    elapsed = time.monotonic() - started_at

    assert message is not None
    assert message.text == "populated"
    assert elapsed < 0.5
    assert sms.sms.get_text.await_count == 2
    properties.properties.on_properties_changed.assert_called_once()
    properties.properties.off_properties_changed.assert_called_once()


async def test_read_message_recovers_when_no_signal_fires(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    sms.sms.get_text.side_effect = ["", "populated"]
    properties = make_properties_proxy()
    fake_bus.get_proxy_object.side_effect = [sms, properties]
    client = ModemManagerClient(sms_text_wait_timeout_seconds=10)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    started_at = time.monotonic()
    message = await asyncio.wait_for(client.read_message(SMS_PATH_1), timeout=1.0)
    elapsed = time.monotonic() - started_at

    assert message is not None
    assert message.text == "populated"
    assert elapsed >= 0.5
    assert sms.sms.get_text.await_count == 2
    properties.properties.on_properties_changed.assert_called_once()
    properties.properties.off_properties_changed.assert_called_once()


async def test_read_message_polls_when_properties_lookup_fails(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    sms.sms.get_text.side_effect = ["", "populated"]
    properties_error = DBusError(
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "ModemManager restarted",
    )
    fake_bus.introspect.side_effect = [object(), properties_error]
    fake_bus.get_proxy_object.return_value = sms
    client = ModemManagerClient(sms_text_wait_timeout_seconds=10)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    started_at = time.monotonic()
    message = await asyncio.wait_for(client.read_message(SMS_PATH_1), timeout=1.0)
    elapsed = time.monotonic() - started_at

    assert message is not None
    assert message.text == "populated"
    assert elapsed >= 0.5
    assert sms.sms.get_text.await_count == 2
    assert fake_bus.get_proxy_object.call_count == 1


async def test_read_message_returns_none_on_empty_text_after_timeout(
    fake_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.modem.client.logger", logger)
    sms = make_sms_proxy(
        number="+15550000001",
        text="",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    properties = make_properties_proxy()
    fake_bus.get_proxy_object.side_effect = [sms, properties]
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.05)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    started_at = time.monotonic()
    message = await client.read_message(SMS_PATH_1)
    elapsed = time.monotonic() - started_at

    assert message is None
    assert 0.04 <= elapsed < 0.25
    logger.warning.assert_called_once_with(
        "sms_text_wait_timeout",
        sms_path=SMS_PATH_1,
        timeout_seconds=0.05,
    )
    properties.properties.off_properties_changed.assert_called_once()


async def test_read_message_skips_non_inbound_pdu(
    fake_bus: MagicMock,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
        pdu_type="submit",
    )
    fake_bus.get_proxy_object.return_value = sms
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    message = await client.read_message(SMS_PATH_1)

    assert message is None
    sms.sms.get_number.assert_not_awaited()
    sms.sms.get_text.assert_not_awaited()
    sms.sms.get_timestamp.assert_not_awaited()


async def test_read_message_subscribes_before_first_read(
    fake_bus: MagicMock,
) -> None:
    calls: list[str] = []
    sms = make_sms_proxy(
        number="+15550000001",
        text="message",
        timestamp="2026-04-26T10:41:00+00:00",
    )

    async def get_text() -> str:
        calls.append("read")
        return "message"

    def on_properties_changed(callback: object) -> None:
        assert callable(callback)
        calls.append("subscribe")

    sms.sms.get_text.side_effect = get_text
    properties = make_properties_proxy()
    properties.properties.on_properties_changed.side_effect = on_properties_changed
    fake_bus.get_proxy_object.side_effect = [sms, properties]
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    message = await client.read_message(SMS_PATH_1)

    assert message is not None
    assert message.text == "message"
    assert calls == ["subscribe", "read"]


@pytest.mark.parametrize(
    "exit_path",
    ["happy", "polling", "signal", "timeout", "exception"],
)
async def test_read_message_unsubscribes_on_all_exit_paths(
    fake_bus: MagicMock,
    exit_path: str,
) -> None:
    sms = make_sms_proxy(
        number="+15550000001",
        text="",
        timestamp="2026-04-26T10:41:00+00:00",
    )
    properties = make_properties_proxy()

    if exit_path == "happy":
        sms.sms.get_text.return_value = "message"
    elif exit_path == "polling":
        sms.sms.get_text.side_effect = ["", "message"]
    elif exit_path == "signal":
        sms.sms.get_text.side_effect = ["", "message"]

        def on_properties_changed(callback: object) -> None:
            assert callable(callback)
            asyncio.get_running_loop().call_soon(callback, SMS_INTERFACE, {"Text": object()}, [])

        properties.properties.on_properties_changed.side_effect = on_properties_changed
    elif exit_path == "timeout":
        sms.sms.get_text.return_value = ""
    else:
        sms.sms.get_text.side_effect = DBusError(
            "org.freedesktop.DBus.Error.ServiceUnknown",
            "ModemManager restarted",
        )

    fake_bus.get_proxy_object.side_effect = [sms, properties]
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    if exit_path == "exception":
        with pytest.raises(Exception, match="failed to read required modem property Text"):
            await client.read_message(SMS_PATH_1)
    else:
        await client.read_message(SMS_PATH_1)

    properties.properties.off_properties_changed.assert_called_once()


async def test_read_message_returns_none_when_sms_object_vanishes(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.UnknownObject", "SMS vanished")
    fake_bus.introspect.side_effect = error
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    message = await client.read_message(SMS_PATH_1)

    assert message is None
    fake_bus.get_proxy_object.assert_not_called()


async def test_read_message_propagates_sms_lookup_transport_failures(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "ModemManager restarted")
    fake_bus.introspect.side_effect = error
    client = ModemManagerClient(sms_text_wait_timeout_seconds=0.01)
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query SMS object {SMS_PATH_1}",
    ) as exc:
        await client.read_message(SMS_PATH_1)

    assert exc.value.__cause__ is error


async def test_delete_message_succeeds_silently_on_happy_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    await client.delete_message(SMS_PATH_1)

    fake_messaging_proxy.messaging.call_delete.assert_awaited_once_with(SMS_PATH_1)


async def test_delete_message_recovers_from_interface_not_found_on_cached_path(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    stale_proxy = MagicMock()
    stale_proxy.get_interface.side_effect = InterfaceNotFoundError(MESSAGING_INTERFACE)
    fake_bus.get_proxy_object.side_effect = [stale_proxy, fake_messaging_proxy]
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    async def refresh_modem() -> str:
        client._modem_path = REFRESHED_MODEM_PATH
        return REFRESHED_MODEM_PATH

    client.find_modem = AsyncMock(side_effect=refresh_modem)

    await client.delete_message(SMS_PATH_1)

    assert client._modem_path == REFRESHED_MODEM_PATH
    client.find_modem.assert_awaited_once_with()
    fake_messaging_proxy.messaging.call_delete.assert_awaited_once_with(SMS_PATH_1)


async def test_delete_message_wraps_dbus_error(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    fake_messaging_proxy.messaging.call_delete.side_effect = DBusError(
        "org.freedesktop.DBus.Error.Failed",
        "delete failed",
    )
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(MessageDeleteFailed, match="delete failed"):
        await client.delete_message(SMS_PATH_1)


async def test_list_messages_wraps_messaging_lookup_failures(
    fake_bus: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "ModemManager restarted")
    fake_bus.introspect.side_effect = error
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query messaging object {MODEM_PATH}",
    ) as exc:
        await client.list_messages()

    assert exc.value.__cause__ is error


async def test_list_messages_wraps_sms_lookup_failures(
    fake_bus: MagicMock,
    fake_messaging_proxy: MagicMock,
) -> None:
    error = DBusError("org.freedesktop.DBus.Error.UnknownObject", "SMS vanished")
    fake_messaging_proxy.messaging.get_messages.return_value = [SMS_PATH_1]
    fake_bus.introspect.side_effect = [object(), error]
    fake_bus.get_proxy_object.return_value = fake_messaging_proxy
    client = ModemManagerClient()
    client._bus = fake_bus
    client._modem_path = MODEM_PATH

    with pytest.raises(
        ModemManagerUnavailable,
        match=f"failed to query SMS object {SMS_PATH_1}",
    ) as exc:
        await client.list_messages()

    assert exc.value.__cause__ is error
