from __future__ import annotations


class TelegramError(Exception):
    """Base exception for Telegram client failures"""


class TelegramRateLimited(TelegramError):  # noqa: N818
    """Raised when Telegram asks the client to retry later."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramAuthError(TelegramError):
    """Raised when Telegram rejects the bot token."""


class TelegramTransportError(TelegramError):
    """Raised when Telegram cannot be reached or returns a retryable error."""
