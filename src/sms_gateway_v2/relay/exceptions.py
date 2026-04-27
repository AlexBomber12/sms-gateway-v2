from __future__ import annotations


class RelayError(Exception):
    """Base exception for SmsRelay failures."""


class RelayNotRunning(RelayError):  # noqa: N818
    """Raised when an operation requires a running relay."""
