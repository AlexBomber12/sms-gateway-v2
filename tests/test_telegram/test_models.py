from __future__ import annotations

import pytest
from pydantic import ValidationError

from sms_gateway_v2.telegram import TelegramMessage
from sms_gateway_v2.telegram.models import MAX_TELEGRAM_TEXT_LENGTH, _telegram_text_length


def test_telegram_message_rejects_empty_text() -> None:
    with pytest.raises(ValidationError, match="text must not be empty"):
        TelegramMessage(chat_id="123", text="")


def test_telegram_message_rejects_text_over_telegram_limit() -> None:
    with pytest.raises(ValidationError, match="4096"):
        TelegramMessage(chat_id="123", text="x" * (MAX_TELEGRAM_TEXT_LENGTH + 1))


def test_telegram_message_allows_escaped_text_at_parsed_telegram_limit() -> None:
    text = "&amp;" * MAX_TELEGRAM_TEXT_LENGTH

    message = TelegramMessage(chat_id="123", text=text)

    assert message.text == text


def test_telegram_message_rejects_escaped_text_over_parsed_telegram_limit() -> None:
    with pytest.raises(ValidationError, match="4096"):
        TelegramMessage(chat_id="123", text="&amp;" * (MAX_TELEGRAM_TEXT_LENGTH + 1))


def test_telegram_message_rejects_html_text_empty_after_entities_parsing() -> None:
    with pytest.raises(ValidationError, match="empty after entities parsing"):
        TelegramMessage(chat_id="123", text="<b></b>")


def test_telegram_message_parse_mode_none_uses_raw_length() -> None:
    with pytest.raises(ValidationError, match="4096"):
        TelegramMessage(
            chat_id="123",
            text="x" * (MAX_TELEGRAM_TEXT_LENGTH + 1),
            parse_mode=None,
        )


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

    assert _telegram_text_length(message.text, "HTML") == MAX_TELEGRAM_TEXT_LENGTH
    assert message.text.endswith("...")


def test_from_sms_does_not_truncate_for_escaped_chars_within_parsed_limit() -> None:
    number = "+1"
    text = "&" * (MAX_TELEGRAM_TEXT_LENGTH - len(number) - len("\n"))

    message = TelegramMessage.from_sms(chat_id="123", number=number, text=text)

    assert _telegram_text_length(message.text, "HTML") == MAX_TELEGRAM_TEXT_LENGTH
    assert message.text.count("&amp;") == len(text)
    assert not message.text.endswith("...")


def test_from_sms_truncation_does_not_split_html_entities() -> None:
    message = TelegramMessage.from_sms(
        chat_id="123",
        number="+1",
        text=("x" * (MAX_TELEGRAM_TEXT_LENGTH - len("+1") - len("\n") - len("...") - 1)) + "&tail",
    )

    assert _telegram_text_length(message.text, "HTML") == MAX_TELEGRAM_TEXT_LENGTH
    assert message.text.endswith("...")
    assert "&..." not in message.text


def test_from_sms_truncates_long_number_without_splitting_entities() -> None:
    message = TelegramMessage.from_sms(
        chat_id="123",
        number=("x" * (MAX_TELEGRAM_TEXT_LENGTH - len("\n") - len("...") - 1)) + "&tail",
        text="hello",
    )

    assert _telegram_text_length(message.text, "HTML") == MAX_TELEGRAM_TEXT_LENGTH
    assert message.text.startswith("<b>")
    assert message.text.endswith("</b>\n")
    assert "&..." not in message.text
