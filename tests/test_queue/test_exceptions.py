from __future__ import annotations

from sms_gateway_v2.queue import DuplicateMessage, ItemNotFound, QueueCorrupted, QueueError


def test_queue_errors_share_base_exception() -> None:
    assert issubclass(DuplicateMessage, QueueError)
    assert issubclass(QueueCorrupted, QueueError)
    assert issubclass(ItemNotFound, QueueError)


def test_queue_error_messages_are_preserved() -> None:
    error = QueueCorrupted("bad item")

    assert str(error) == "bad item"
