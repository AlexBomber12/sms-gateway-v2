from __future__ import annotations

import pytest
from pydantic import ValidationError

from sms_gateway_v2.telegram import TelegramMessage


def test_telegram_message_rejects_empty_text() -> None:
    with pytest.raises(ValidationError, match="text must not be empty"):
        TelegramMessage(chat_id="123", text="")


def test_telegram_message_rejects_text_over_telegram_limit() -> None:
    with pytest.raises(ValidationError, match="4096"):
        TelegramMessage(chat_id="123", text="x" * 4097)


def test_telegram_message_accepts_parse_mode_none() -> None:
    message = TelegramMessage(chat_id="123", text="hello", parse_mode=None)

    assert message.parse_mode is None


def test_telegram_message_defaults_parse_mode_to_html() -> None:
    message = TelegramMessage(chat_id="123", text="hello")

    assert message.parse_mode == "HTML"


def test_from_sms_html_escapes_number_and_text() -> None:
    message = TelegramMessage.from_sms(
        chat_id="123",
        number='<+1&"2">',
        text='hello <world> & "friends"',
    )

    assert (
        message.text
        == "<b>&lt;+1&amp;&quot;2&quot;&gt;</b>\nhello &lt;world&gt; &amp; &quot;friends&quot;"
    )
    assert message.parse_mode == "HTML"


def test_from_sms_truncates_with_ellipsis_at_telegram_limit() -> None:
    message = TelegramMessage.from_sms(chat_id="123", number="+15551234567", text="x" * 5000)

    assert len(message.text) == 4096
    assert message.text.endswith("...")


def test_from_sms_truncation_does_not_split_html_entities() -> None:
    message = TelegramMessage.from_sms(chat_id="123", number="+1", text=("x" * 4082) + "&")

    assert len(message.text) <= 4096
    assert message.text.endswith("...")
    assert "&..." not in message.text


def test_from_sms_truncates_long_number_without_splitting_entities() -> None:
    message = TelegramMessage.from_sms(chat_id="123", number=("x" * 4084) + "&", text="hello")

    assert len(message.text) <= 4096
    assert message.text.startswith("<b>")
    assert message.text.endswith("</b>\n")
    assert "&..." not in message.text
