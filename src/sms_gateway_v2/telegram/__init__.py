from __future__ import annotations

from sms_gateway_v2.telegram.exceptions import (
    TelegramAuthError,
    TelegramError,
    TelegramRateLimited,
    TelegramTransportError,
)

__all__ = [
    "TelegramAuthError",
    "TelegramError",
    "TelegramRateLimited",
    "TelegramTransportError",
]
