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
            body = body[: MAX_TELEGRAM_TEXT_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
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
