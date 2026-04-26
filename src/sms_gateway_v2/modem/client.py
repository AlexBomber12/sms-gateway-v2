from __future__ import annotations

import time

import structlog
from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.errors import AuthError, DBusError, InvalidAddressError

from sms_gateway_v2.modem.exceptions import ModemManagerUnavailable

logger = structlog.get_logger(__name__)


class ModemManagerClient:
    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._modem_path: str | None = None

    async def connect(self) -> None:
        if self._bus is not None and self._bus.connected:
            return

        started_at = time.monotonic()
        try:
            bus = MessageBus(bus_type=BusType.SYSTEM)
            self._bus = await bus.connect()
        except (OSError, AuthError, DBusError, InvalidAddressError) as exc:
            raise ModemManagerUnavailable("failed to connect to system D-Bus") from exc

        logger.info(
            "client_connected",
            duration_seconds=time.monotonic() - started_at,
        )

    async def disconnect(self) -> None:
        if self._bus is None:
            return

        self._bus.disconnect()
        self._bus = None
        self._modem_path = None
        logger.info("client_disconnected")
