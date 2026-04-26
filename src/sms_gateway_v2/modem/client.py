from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, TypeVar, cast

import structlog
from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.errors import (
    AuthError,
    DBusError,
    InterfaceNotFoundError,
    InvalidAddressError,
    InvalidBusNameError,
    InvalidInterfaceNameError,
    InvalidIntrospectionError,
    InvalidMemberNameError,
    InvalidMessageError,
    InvalidObjectPathError,
    InvalidSignatureError,
    SignalDisabledError,
    SignatureBodyMismatchError,
)

from sms_gateway_v2.modem.exceptions import (
    MessageDeleteFailed,
    ModemError,
    ModemManagerUnavailable,
    ModemNotFound,
)
from sms_gateway_v2.modem.models import IncomingSms, ModemInfo, RegistrationState, SignalQuality

logger = structlog.get_logger(__name__)

MODEM_MANAGER_BUS_NAME = "org.freedesktop.ModemManager1"
MODEM_MANAGER_OBJECT_PATH = "/org/freedesktop/ModemManager1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_3GPP_INTERFACE = "org.freedesktop.ModemManager1.Modem.Modem3gpp"
MESSAGING_INTERFACE = "org.freedesktop.ModemManager1.Modem.Messaging"
SMS_INTERFACE = "org.freedesktop.ModemManager1.Sms"
SIM_INTERFACE = "org.freedesktop.ModemManager1.Sim"
DBUS_OPERATION_ERRORS = (
    OSError,
    AuthError,
    DBusError,
    InterfaceNotFoundError,
    InvalidAddressError,
    InvalidBusNameError,
    InvalidInterfaceNameError,
    InvalidIntrospectionError,
    InvalidMemberNameError,
    InvalidMessageError,
    InvalidObjectPathError,
    InvalidSignatureError,
    SignalDisabledError,
    SignatureBodyMismatchError,
)

ManagedObjects = dict[str, dict[str, object]]
PropertyValue = TypeVar("PropertyValue")


class ObjectManagerInterface(Protocol):
    async def call_get_managed_objects(self) -> ManagedObjects: ...


class ModemInterface(Protocol):
    async def get_manufacturer(self) -> str: ...

    async def get_model(self) -> str: ...

    async def get_equipment_identifier(self) -> str: ...

    async def get_primary_port(self) -> str: ...

    async def get_state(self) -> str: ...

    async def get_signal_quality(self) -> tuple[int, bool]: ...

    async def get_sim(self) -> str: ...


class Modem3gppInterface(Protocol):
    async def get_registration_state(self) -> int: ...

    async def get_operator_name(self) -> str: ...

    async def get_operator_code(self) -> str: ...


class SimInterface(Protocol):
    async def get_imsi(self) -> str: ...

    async def get_operator_name(self) -> str: ...

    async def get_operator_identifier(self) -> str: ...


class MessagingInterface(Protocol):
    async def get_messages(self) -> list[str]: ...

    async def call_delete(self, sms_path: str) -> None: ...

    def on_added(self, callback: Callable[[str, bool], None]) -> None: ...


class SmsInterface(Protocol):
    async def get_number(self) -> str: ...

    async def get_text(self) -> str: ...

    async def get_timestamp(self) -> str: ...

    async def get_pdu_type(self) -> str: ...


class ModemManagerClient:
    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._modem_path: str | None = None
        self._watch_tasks: set[asyncio.Future[None]] = set()

    async def connect(self) -> None:
        if self._bus is not None and self._bus.connected:
            return

        started_at = time.monotonic()
        try:
            bus = MessageBus(bus_type=BusType.SYSTEM)
            self._bus = await bus.connect()
        except DBUS_OPERATION_ERRORS as exc:
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
        try:
            introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, MODEM_MANAGER_OBJECT_PATH)
            proxy = bus.get_proxy_object(
                MODEM_MANAGER_BUS_NAME,
                MODEM_MANAGER_OBJECT_PATH,
                introspection,
            )
            object_manager = cast(
                ObjectManagerInterface, proxy.get_interface(OBJECT_MANAGER_INTERFACE)
            )
            managed_objects: ManagedObjects = await object_manager.call_get_managed_objects()
        except DBUS_OPERATION_ERRORS as exc:
            raise ModemManagerUnavailable("failed to query ModemManager objects") from exc

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

    async def get_modem_info(self) -> ModemInfo:
        started_at = time.monotonic()
        modem_path = await self._ensure_modem_path()

        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, modem_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, modem_path, introspection)
        modem = cast(ModemInterface, proxy.get_interface(MODEM_INTERFACE))
        modem_3gpp = cast(Modem3gppInterface, proxy.get_interface(MODEM_3GPP_INTERFACE))

        manufacturer = await self._read_required("Manufacturer", modem.get_manufacturer)
        model = await self._read_required("Model", modem.get_model)
        equipment_id = await self._read_required(
            "EquipmentIdentifier",
            modem.get_equipment_identifier,
        )
        device = await self._read_required("PrimaryPort", modem.get_primary_port)
        state = await self._read_required("State", modem.get_state)
        signal_percent, signal_recent = await self._read_required(
            "SignalQuality",
            modem.get_signal_quality,
        )
        registration_value = await self._read_required(
            "RegistrationState",
            modem_3gpp.get_registration_state,
        )
        await self._read_required("OperatorName", modem_3gpp.get_operator_name)
        await self._read_required("OperatorCode", modem_3gpp.get_operator_code)
        sim_path = await self._read_required("Sim", modem.get_sim)
        sim = await self._get_sim_interface(sim_path)

        info = ModemInfo(
            object_path=modem_path,
            manufacturer=manufacturer,
            model=model,
            equipment_id=equipment_id,
            device=device,
            state=state,
            registration=RegistrationState.from_dbus_value(registration_value),
            signal=SignalQuality(percent=signal_percent, recent=signal_recent),
            sim_imsi=await self._read_optional(sim.get_imsi),
            sim_operator_name=await self._read_optional(sim.get_operator_name),
            sim_operator_id=await self._read_optional(sim.get_operator_identifier),
        )
        logger.info(
            "modem_info_read",
            duration_seconds=time.monotonic() - started_at,
            modem_path=modem_path,
        )
        return info

    async def get_signal_quality(self) -> SignalQuality:
        started_at = time.monotonic()
        modem_path = await self._ensure_modem_path()
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, modem_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, modem_path, introspection)
        modem = cast(ModemInterface, proxy.get_interface(MODEM_INTERFACE))

        signal_percent, signal_recent = await self._read_required(
            "SignalQuality",
            modem.get_signal_quality,
        )
        signal = SignalQuality(percent=signal_percent, recent=signal_recent)
        logger.info(
            "signal_read",
            duration_seconds=time.monotonic() - started_at,
            modem_path=modem_path,
            percent=signal.percent,
            recent=signal.recent,
        )
        return signal

    async def get_registration_state(self) -> RegistrationState:
        started_at = time.monotonic()
        modem_path = await self._ensure_modem_path()
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, modem_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, modem_path, introspection)
        modem_3gpp = cast(Modem3gppInterface, proxy.get_interface(MODEM_3GPP_INTERFACE))

        registration_value = await self._read_required(
            "RegistrationState",
            modem_3gpp.get_registration_state,
        )
        registration = RegistrationState.from_dbus_value(registration_value)
        logger.info(
            "registration_read",
            duration_seconds=time.monotonic() - started_at,
            modem_path=modem_path,
            registration=registration.value,
        )
        return registration

    async def list_messages(self) -> list[IncomingSms]:
        modem_path = await self._ensure_modem_path()
        messaging = await self._get_messaging_interface(modem_path)
        sms_paths = await self._read_required("Messages", messaging.get_messages)
        messages: list[IncomingSms] = []

        for sms_path in sms_paths:
            sms = await self._get_sms_interface(sms_path)
            message = IncomingSms(
                object_path=sms_path,
                number=await self._read_required("Number", sms.get_number),
                text=await self._read_required("Text", sms.get_text),
                timestamp=self._parse_timestamp(await self._read_optional(sms.get_timestamp)),
                pdu_type=await self._read_required("PduType", sms.get_pdu_type),
            )
            messages.append(message)
            logger.info(
                "message_listed",
                modem_path=modem_path,
                sms_path=sms_path,
            )

        if all(message.timestamp is not None for message in messages):
            messages.sort(key=lambda message: cast(datetime, message.timestamp))
        else:
            messages.sort(key=lambda message: message.object_path)
        return messages

    async def delete_message(self, sms_path: str) -> None:
        modem_path = await self._ensure_modem_path()
        messaging = await self._get_messaging_interface(modem_path)
        try:
            await messaging.call_delete(sms_path)
        except DBUS_OPERATION_ERRORS as exc:
            raise MessageDeleteFailed(f"failed to delete SMS {sms_path}: {exc}") from exc
        logger.info(
            "message_deleted",
            modem_path=modem_path,
            sms_path=sms_path,
        )

    async def watch_added(self, callback: Callable[[str], Awaitable[None]]) -> None:
        modem_path = await self._ensure_modem_path()
        messaging = await self._get_messaging_interface(modem_path)

        def handle_added(sms_path: str, received: bool) -> None:
            logger.info(
                "message_added_signal_received",
                modem_path=modem_path,
                sms_path=sms_path,
                received=received,
            )
            if not received:
                return
            task = asyncio.ensure_future(callback(sms_path))
            self._watch_tasks.add(task)
            task.add_done_callback(self._watch_tasks.discard)

        messaging.on_added(handle_added)

    def _require_bus(self) -> MessageBus:
        if self._bus is None:
            raise ModemManagerUnavailable("not connected to system D-Bus")
        return self._bus

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    async def _ensure_modem_path(self) -> str:
        if self._modem_path is None:
            await self.find_modem()
        modem_path = self._modem_path
        assert modem_path is not None
        return modem_path

    async def _get_sim_interface(self, sim_path: str) -> SimInterface:
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, sim_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, sim_path, introspection)
        return cast(SimInterface, proxy.get_interface(SIM_INTERFACE))

    async def _get_messaging_interface(self, modem_path: str) -> MessagingInterface:
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, modem_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, modem_path, introspection)
        return cast(MessagingInterface, proxy.get_interface(MESSAGING_INTERFACE))

    async def _get_sms_interface(self, sms_path: str) -> SmsInterface:
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, sms_path)
        proxy = bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, sms_path, introspection)
        return cast(SmsInterface, proxy.get_interface(SMS_INTERFACE))

    async def _read_required(
        self,
        property_name: str,
        reader: Callable[[], Awaitable[PropertyValue]],
    ) -> PropertyValue:
        try:
            return await reader()
        except DBUS_OPERATION_ERRORS as exc:
            raise ModemError(f"failed to read required modem property {property_name}") from exc

    async def _read_optional(
        self,
        reader: Callable[[], Awaitable[str]],
    ) -> str | None:
        try:
            return await reader()
        except DBUS_OPERATION_ERRORS:
            return None
