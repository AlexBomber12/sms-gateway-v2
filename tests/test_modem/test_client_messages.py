from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import MessageDeleteFailed, ModemManagerClient, ModemManagerUnavailable

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
REFRESHED_MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/1"
SMS_PATH_1 = "/org/freedesktop/ModemManager1/SMS/1"
SMS_PATH_2 = "/org/freedesktop/ModemManager1/SMS/2"
SMS_PATH_10 = "/org/freedesktop/ModemManager1/SMS/10"


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
