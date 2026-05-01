from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def make_fake_messaging_proxy() -> MagicMock:
    messaging = MagicMock()
    messaging.get_messages = AsyncMock(return_value=[])
    messaging.call_delete = AsyncMock(return_value=None)
    messaging.added_handler = None

    def register_added(handler: object) -> None:
        messaging.added_handler = handler

    def unregister_added(handler: object) -> None:
        if messaging.added_handler is handler:
            messaging.added_handler = None

    messaging.on_added = MagicMock(side_effect=register_added)
    messaging.off_added = MagicMock(side_effect=unregister_added)

    proxy = MagicMock()
    proxy.messaging = messaging
    proxy.get_interface.return_value = messaging
    return proxy
