from __future__ import annotations

import time
from typing import Protocol, cast

import structlog
from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.errors import AuthError, DBusError, InvalidAddressError

from sms_gateway_v2.modem.exceptions import ModemManagerUnavailable, ModemNotFound

logger = structlog.get_logger(__name__)

MODEM_MANAGER_BUS_NAME = "org.freedesktop.ModemManager1"
MODEM_MANAGER_OBJECT_PATH = "/org/freedesktop/ModemManager1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"

ManagedObjects = dict[str, dict[str, object]]


class ObjectManagerInterface(Protocol):
    async def call_get_managed_objects(self) -> ManagedObjects: ...


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

    async def find_modem(self) -> str:
        bus = self._require_bus()
        started_at = time.monotonic()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, MODEM_MANAGER_OBJECT_PATH)
        proxy = bus.get_proxy_object(
            MODEM_MANAGER_BUS_NAME,
            MODEM_MANAGER_OBJECT_PATH,
            introspection,
        )
        object_manager = cast(ObjectManagerInterface, proxy.get_interface(OBJECT_MANAGER_INTERFACE))
        managed_objects: ManagedObjects = await object_manager.call_get_managed_objects()

        for object_path, interfaces in managed_objects.items():
            if MODEM_INTERFACE in interfaces:
                self._modem_path = object_path
                logger.info(
                    "modem_found",
                    duration_seconds=time.monotonic() - started_at,
                    modem_path=object_path,
                )
                return object_path

        logger.warning(
            "modem_not_found",
            duration_seconds=time.monotonic() - started_at,
        )
        raise ModemNotFound("no ModemManager modem object found")

    def _require_bus(self) -> MessageBus:
        if self._bus is None:
            raise ModemManagerUnavailable("not connected to system D-Bus")
        return self._bus
