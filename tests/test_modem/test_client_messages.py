from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from dbus_fast import DBusError

from sms_gateway_v2.modem import MessageDeleteFailed, ModemManagerClient, ModemManagerUnavailable

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
SMS_PATH_1 = "/org/freedesktop/ModemManager1/SMS/1"
SMS_PATH_2 = "/org/freedesktop/ModemManager1/SMS/2"


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


@pytest.mark.parametrize(
    ("raw_pdu_type", "expected"),
    [
        (0, "unknown"),
        (1, "deliver"),
        (2, "submit"),
        (3, "status-report"),
        (32, "cdma-deliver"),
        (33, "cdma-submit"),
        (34, "cdma-cancellation"),
        (35, "cdma-delivery-acknowledgement"),
        (36, "cdma-user-acknowledgement"),
        (37, "cdma-read-acknowledgement"),
        (999, "unknown"),
        ("deliver", "deliver"),
    ],
)
async def test_list_messages_decodes_pdu_type(
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
