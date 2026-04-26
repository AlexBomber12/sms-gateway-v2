from __future__ import annotations


class QueueError(Exception):
    """Base exception for queue failures."""


class DuplicateMessage(QueueError):  # noqa: N818
    """Raised when enqueueing a message whose content_hash already exists in dedup."""


class QueueCorrupted(QueueError):  # noqa: N818
    """Raised when on-disk JSON is invalid or missing required fields."""


class ItemNotFound(QueueError):  # noqa: N818
    """Raised when claiming or marking an item that does not exist."""
