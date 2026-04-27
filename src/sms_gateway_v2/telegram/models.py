from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

MAX_TELEGRAM_TEXT_LENGTH = 4096
TRUNCATION_SUFFIX = "..."
SMS_SEPARATOR = "\n"


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    chat_id: str
    text: str
    parse_mode: str | None = "HTML"

    @classmethod
    def from_sms(cls, chat_id: str, number: str, text: str) -> Self:
        body = f"<b>{escape(number)}</b>\n{escape(text)}"
        if _sms_body_length(number, text) > MAX_TELEGRAM_TEXT_LENGTH:
            body = _truncate_sms_body(number, text)
        return cls(chat_id=chat_id, text=body)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value == "":
            msg = "text must not be empty"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_text_length(self) -> Self:
        text_length = _telegram_text_length(self.text, self.parse_mode)
        if text_length == 0:
            msg = "text must not be empty after entities parsing"
            raise ValueError(msg)
        if text_length > MAX_TELEGRAM_TEXT_LENGTH:
            msg = "text must be 4096 characters or fewer after entities parsing"
            raise ValueError(msg)
        return self


def _truncate_sms_body(number: str, text: str) -> str:
    text_limit = (
        MAX_TELEGRAM_TEXT_LENGTH - len(number) - len(SMS_SEPARATOR) - len(TRUNCATION_SUFFIX)
    )
    if text_limit >= 0:
        escaped_text = escape(text[:text_limit])
        return f"<b>{escape(number)}</b>\n{escaped_text}{TRUNCATION_SUFFIX}"

    number_limit = MAX_TELEGRAM_TEXT_LENGTH - len(SMS_SEPARATOR) - len(TRUNCATION_SUFFIX)
    truncated_number = escape(number[:number_limit])
    return f"<b>{truncated_number}{TRUNCATION_SUFFIX}</b>\n"


def _sms_body_length(number: str, text: str) -> int:
    return len(number) + len(SMS_SEPARATOR) + len(text)


def _telegram_text_length(text: str, parse_mode: str | None) -> int:
    if parse_mode != "HTML":
        return len(text)

    parser = _HtmlTextLengthParser()
    parser.feed(text)
    parser.close()
    return parser.length


class _HtmlTextLengthParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data)
