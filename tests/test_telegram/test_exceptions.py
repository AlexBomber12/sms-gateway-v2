from __future__ import annotations

from sms_gateway_v2.telegram import (
    TelegramAuthError,
    TelegramError,
    TelegramRateLimited,
    TelegramTransportError,
)


def test_telegram_exceptions_inherit_from_base_error() -> None:
    assert issubclass(TelegramRateLimited, TelegramError)
    assert issubclass(TelegramAuthError, TelegramError)
    assert issubclass(TelegramTransportError, TelegramError)


def test_telegram_rate_limited_stores_retry_after() -> None:
    error = TelegramRateLimited("too many requests", retry_after=2.5)

    assert str(error) == "too many requests"
    assert error.retry_after == 2.5
