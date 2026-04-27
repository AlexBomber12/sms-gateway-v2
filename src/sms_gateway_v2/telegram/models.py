from __future__ import annotations

from html import escape
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

MAX_TELEGRAM_TEXT_LENGTH = 4096
TRUNCATION_SUFFIX = "..."


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    chat_id: str
    text: str
    parse_mode: str | None = "HTML"

    @classmethod
    def from_sms(cls, chat_id: str, number: str, text: str) -> Self:
        body = f"<b>{escape(number)}</b>\n{escape(text)}"
        if len(body) > MAX_TELEGRAM_TEXT_LENGTH:
            body = _truncate_sms_body(number, text)
        return cls(chat_id=chat_id, text=body)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value == "":
            msg = "text must not be empty"
            raise ValueError(msg)
        if len(value) > MAX_TELEGRAM_TEXT_LENGTH:
            msg = "text must be 4096 characters or fewer"
            raise ValueError(msg)
        return value


def _truncate_sms_body(number: str, text: str) -> str:
    escaped_number = escape(number)
    prefix = f"<b>{escaped_number}</b>\n"
    text_limit = MAX_TELEGRAM_TEXT_LENGTH - len(prefix) - len(TRUNCATION_SUFFIX)
    if text_limit >= 0:
        escaped_text = _escape_prefix(text, text_limit)
        return f"{prefix}{escaped_text}{TRUNCATION_SUFFIX}"

    number_limit = MAX_TELEGRAM_TEXT_LENGTH - len("<b></b>\n") - len(TRUNCATION_SUFFIX)
    truncated_number = _escape_prefix(number, number_limit)
    return f"<b>{truncated_number}{TRUNCATION_SUFFIX}</b>\n"


def _escape_prefix(value: str, max_length: int) -> str:
    escaped_parts: list[str] = []
    escaped_length = 0
    for character in value:
        escaped_character = escape(character)
        if escaped_length + len(escaped_character) > max_length:
            break
        escaped_parts.append(escaped_character)
        escaped_length += len(escaped_character)
    return "".join(escaped_parts)
