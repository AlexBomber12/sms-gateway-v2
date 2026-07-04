from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime
from types import MethodType
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

from sms_gateway_v2.config import get_settings
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
DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
MODEM_INTERFACE = "org.freedesktop.ModemManager1.Modem"
MODEM_3GPP_INTERFACE = "org.freedesktop.ModemManager1.Modem.Modem3gpp"
MESSAGING_INTERFACE = "org.freedesktop.ModemManager1.Modem.Messaging"
SMS_INTERFACE = "org.freedesktop.ModemManager1.Sms"
SIM_INTERFACE = "org.freedesktop.ModemManager1.Sim"
UNKNOWN_OBJECT_ERROR = "org.freedesktop.DBus.Error.UnknownObject"
UNKNOWN_PROPERTY_ERROR = "org.freedesktop.DBus.Error.UnknownProperty"
MODEM_STATES = {
    -1: "failed",
    0: "unknown",
    1: "initializing",
    2: "locked",
    3: "disabled",
    4: "disabling",
    5: "enabling",
    6: "enabled",
    7: "searching",
    8: "registered",
    9: "disconnecting",
    10: "connecting",
    11: "connected",
}
SMS_PDU_TYPES = {
    0: "unknown",
    1: "deliver",
    2: "submit",
    3: "status-report",
    32: "cdma-deliver",
    33: "cdma-submit",
    34: "cdma-cancellation",
    35: "cdma-delivery-acknowledgement",
    36: "cdma-user-acknowledgement",
    37: "cdma-read-acknowledgement",
}
INBOUND_SMS_PDU_TYPES = {"deliver", "cdma-deliver"}
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
AddedCallback = Callable[[str], Awaitable[None]]
CallbackKey = tuple[str, int, int]
PropertiesChangedCallback = Callable[[str, dict[str, object], list[str]], None]


class ProxyObject(Protocol):
    def get_interface(self, interface_name: str) -> object: ...


class ObjectManagerInterface(Protocol):
    async def call_get_managed_objects(self) -> ManagedObjects: ...


class ModemInterface(Protocol):
    async def get_manufacturer(self) -> str: ...

    async def get_model(self) -> str: ...

    async def get_equipment_identifier(self) -> str: ...

    async def get_primary_port(self) -> str: ...

    async def get_state(self) -> int | str: ...

    async def get_signal_quality(self) -> tuple[int, bool]: ...

    async def get_sim(self) -> str: ...

    async def call_reset(self) -> None: ...


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

    def off_added(self, callback: Callable[[str, bool], None]) -> None: ...


class SmsInterface(Protocol):
    async def get_number(self) -> str: ...

    async def get_text(self) -> str: ...

    async def get_timestamp(self) -> str: ...

    async def get_pdu_type(self) -> int | str: ...


class DBusPropertiesInterface(Protocol):
    def on_properties_changed(self, callback: PropertiesChangedCallback) -> None: ...

    def off_properties_changed(self, callback: PropertiesChangedCallback) -> None: ...


class ModemManagerClient:
    def __init__(self, sms_text_wait_timeout_seconds: float | None = None) -> None:
        self._bus: MessageBus | None = None
        self._modem_path: str | None = None
        self._sms_text_wait_timeout_seconds = sms_text_wait_timeout_seconds
        self._watch_tasks: set[asyncio.Future[None]] = set()
        self._added_callbacks: dict[CallbackKey, AddedCallback] = {}
        self._added_watch_keys: set[tuple[str, CallbackKey]] = set()
        self._added_watch_handlers: dict[
            tuple[str, CallbackKey],
            tuple[MessagingInterface, Callable[[str, bool], None]],
        ] = {}
        self._added_watch_resubscribe_required = False

    async def connect(self) -> None:
        if self._bus is not None and self._bus.connected:
            if self._needs_added_watch_resubscribe():
                self._added_watch_resubscribe_required = True
            await self._resubscribe_added_watchers_after_reconnect()
            return

        if self._bus is not None:
            self._bus = None
            self._modem_path = None
            self._added_watch_keys.clear()
            self._added_watch_handlers.clear()
            self._added_watch_resubscribe_required = bool(self._added_callbacks)

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
        if self._needs_added_watch_resubscribe():
            self._added_watch_resubscribe_required = True
        await self._resubscribe_added_watchers_after_reconnect()

    async def disconnect(self) -> None:
        if self._bus is None:
            return

        self._unsubscribe_all_added_watchers()
        self._bus.disconnect()
        self._bus = None
        self._modem_path = None
        self._added_callbacks.clear()
        self._added_watch_resubscribe_required = False
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
        modem_path, interfaces = await self._get_modem_interfaces(
            (MODEM_INTERFACE, MODEM_3GPP_INTERFACE)
        )
        modem, modem_3gpp = interfaces
        modem = cast(ModemInterface, modem)
        modem_3gpp = cast(
            Modem3gppInterface,
            modem_3gpp,
        )

        manufacturer = await self._read_required("Manufacturer", modem.get_manufacturer)
        model = await self._read_required("Model", modem.get_model)
        equipment_id = await self._read_required(
            "EquipmentIdentifier",
            modem.get_equipment_identifier,
        )
        device = await self._read_required("PrimaryPort", modem.get_primary_port)
        state = self._decode_modem_state(await self._read_required("State", modem.get_state))
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
        sim_imsi: str | None = None
        sim_operator_name: str | None = None
        sim_operator_id: str | None = None
        if sim_path not in {"", "/"}:
            sim = await self._get_sim_interface(sim_path)
            sim_imsi = await self._read_optional("Imsi", sim.get_imsi)
            sim_operator_name = await self._read_optional("OperatorName", sim.get_operator_name)
            sim_operator_id = await self._read_optional(
                "OperatorIdentifier",
                sim.get_operator_identifier,
            )

        info = ModemInfo(
            object_path=modem_path,
            manufacturer=manufacturer,
            model=model,
            equipment_id=equipment_id,
            device=device,
            state=state,
            registration=RegistrationState.from_dbus_value(registration_value),
            signal=SignalQuality(percent=signal_percent, recent=signal_recent),
            sim_imsi=sim_imsi,
            sim_operator_name=sim_operator_name,
            sim_operator_id=sim_operator_id,
        )
        logger.info(
            "modem_info_read",
            duration_seconds=time.monotonic() - started_at,
            modem_path=modem_path,
        )
        return info

    async def get_signal_quality(self) -> SignalQuality:
        started_at = time.monotonic()
        modem_path, modem = await self._get_modem_interface(MODEM_INTERFACE)
        modem = cast(ModemInterface, modem)

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
        modem_path, modem_3gpp = await self._get_modem_interface(MODEM_3GPP_INTERFACE)
        modem_3gpp = cast(Modem3gppInterface, modem_3gpp)

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

    async def reset(self) -> None:
        started_at = time.monotonic()
        modem_path, modem = await self._get_modem_interface(MODEM_INTERFACE)
        modem = cast(ModemInterface, modem)
        try:
            await modem.call_reset()
        except DBUS_OPERATION_ERRORS as exc:
            raise ModemManagerUnavailable(f"failed to reset modem at {modem_path}") from exc

        logger.info(
            "modem_reset_called",
            duration_seconds=time.monotonic() - started_at,
            modem_path=modem_path,
        )
        self._modem_path = None
        self._unsubscribe_all_added_watchers()
        self._added_watch_resubscribe_required = bool(self._added_callbacks)

    async def list_messages(self) -> list[IncomingSms]:
        modem_path = await self._ensure_modem_path()
        modem_path, messaging = await self._get_messaging_interface(modem_path)
        sms_paths = await self._read_required("Messages", messaging.get_messages)
        messages: list[IncomingSms] = []

        for sms_path in sms_paths:
            sms = await self._get_sms_interface(sms_path)
            pdu_type = self._decode_pdu_type(await self._read_required("PduType", sms.get_pdu_type))
            if pdu_type not in INBOUND_SMS_PDU_TYPES:
                logger.info(
                    "message_skipped_non_inbound",
                    modem_path=modem_path,
                    sms_path=sms_path,
                    pdu_type=pdu_type,
                )
                continue
            message = IncomingSms(
                object_path=sms_path,
                number=await self._read_required("Number", sms.get_number),
                text=await self._read_required("Text", sms.get_text),
                timestamp=self._parse_timestamp(
                    await self._read_optional("Timestamp", sms.get_timestamp)
                ),
                pdu_type=pdu_type,
            )
            messages.append(message)
            logger.info(
                "message_listed",
                modem_path=modem_path,
                sms_path=sms_path,
            )

        messages.sort(key=self._message_sort_key)
        return messages

    async def read_message(self, sms_path: str) -> IncomingSms | None:
        """Read one SMS by path, waiting for delayed Text population after MessageAdded.

        Some modems expose the SMS object and emit MessageAdded before ModemManager's async
        decoder has populated the Text property. This waits briefly for the Text property
        change before returning, while still allowing degraded empty-body delivery on timeout.
        """
        try:
            sms = await self._get_sms_interface(sms_path)
        except ModemManagerUnavailable as exc:
            if self._is_unknown_object_unavailable(exc):
                logger.info("message_read_missing", sms_path=sms_path)
                return None
            raise
        pdu_type = self._decode_pdu_type(await self._read_required("PduType", sms.get_pdu_type))
        if pdu_type not in INBOUND_SMS_PDU_TYPES:
            logger.info(
                "message_skipped_non_inbound",
                sms_path=sms_path,
                pdu_type=pdu_type,
            )
            return None

        timeout_seconds = self._sms_text_wait_timeout_seconds
        if timeout_seconds is None:
            timeout_seconds = get_settings().modem_sms_text_wait_timeout_seconds
        text_changed = asyncio.Event()
        properties: DBusPropertiesInterface | None = None

        def handle_properties_changed(
            interface_name: str,
            changed_properties: dict[str, object],
            _invalidated_properties: list[str],
        ) -> None:
            if interface_name == SMS_INTERFACE and "Text" in changed_properties:
                text_changed.set()

        try:
            properties = await self._get_dbus_properties_interface(sms_path)
        except ModemManagerUnavailable:
            text = await self._read_required("Text", sms.get_text)
            if text == "":
                text = await self._wait_for_sms_text(sms_path, sms, text_changed, timeout_seconds)
        else:
            properties.on_properties_changed(handle_properties_changed)
            try:
                text = await self._read_required("Text", sms.get_text)
                if text == "":
                    text = await self._wait_for_sms_text(
                        sms_path,
                        sms,
                        text_changed,
                        timeout_seconds,
                    )
            finally:
                properties.off_properties_changed(handle_properties_changed)

        message = IncomingSms(
            object_path=sms_path,
            number=await self._read_required("Number", sms.get_number),
            text=text,
            timestamp=self._parse_timestamp(
                await self._read_optional("Timestamp", sms.get_timestamp)
            ),
            pdu_type=pdu_type,
        )
        logger.info("message_read", sms_path=sms_path)
        return message

    async def delete_message(self, sms_path: str) -> None:
        modem_path = await self._ensure_modem_path()
        modem_path, messaging = await self._get_messaging_interface(modem_path)
        try:
            await messaging.call_delete(sms_path)
        except DBUS_OPERATION_ERRORS as exc:
            raise MessageDeleteFailed(f"failed to delete SMS {sms_path}: {exc}") from exc
        logger.info(
            "message_deleted",
            modem_path=modem_path,
            sms_path=sms_path,
        )

    async def watch_added(self, callback: AddedCallback) -> None:
        callback_key = self._callback_key(callback)
        callback = self._added_callbacks.setdefault(callback_key, callback)
        modem_path = await self._ensure_modem_path()
        await self._subscribe_added_watch(modem_path, callback_key, callback)

    def _callback_key(self, callback: AddedCallback) -> CallbackKey:
        if isinstance(callback, MethodType):
            return ("method", id(callback.__self__), id(callback.__func__))
        return ("callable", id(callback), 0)

    def _build_added_handler(
        self,
        modem_path: str,
        callback: AddedCallback,
    ) -> Callable[[str, bool], None]:
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

            def handle_task_done(done_task: asyncio.Future[None]) -> None:
                self._watch_tasks.discard(done_task)
                try:
                    done_task.result()
                except Exception as exc:
                    logger.exception(
                        "message_added_callback_failed",
                        modem_path=modem_path,
                        sms_path=sms_path,
                        error=str(exc),
                    )

            task.add_done_callback(handle_task_done)

        return handle_added

    async def _subscribe_added_watch(
        self,
        modem_path: str,
        callback_key: CallbackKey,
        callback: AddedCallback,
    ) -> None:
        watch_key = (modem_path, callback_key)
        if watch_key not in self._added_watch_keys:
            modem_path, messaging = await self._get_messaging_interface(modem_path)
            watch_key = (modem_path, callback_key)
            if watch_key not in self._added_watch_keys:
                handler = self._build_added_handler(modem_path, callback)
                messaging.on_added(handler)
                self._added_watch_keys.add(watch_key)
                self._added_watch_handlers[watch_key] = (messaging, handler)

    def _unsubscribe_added_watcher(self, watch_key: tuple[str, CallbackKey]) -> None:
        self._added_watch_keys.discard(watch_key)
        info = self._added_watch_handlers.pop(watch_key, None)
        if info is not None:
            messaging, handler = info
            try:
                messaging.off_added(handler)
            except DBUS_OPERATION_ERRORS as exc:
                logger.info(
                    "added_watch_unsubscribe_failed",
                    modem_path=watch_key[0],
                    error=str(exc),
                )

    def _unsubscribe_all_added_watchers(self) -> None:
        for watch_key in list(self._added_watch_handlers):
            self._unsubscribe_added_watcher(watch_key)
        self._added_watch_keys.clear()

    async def _resubscribe_added_watchers(self, modem_path: str) -> None:
        self._unsubscribe_all_added_watchers()
        await self._subscribe_missing_added_watchers(modem_path)

    async def _subscribe_missing_added_watchers(self, modem_path: str) -> None:
        self._added_watch_resubscribe_required = bool(self._added_callbacks)
        for callback_key, callback in self._added_callbacks.items():
            await self._subscribe_added_watch(modem_path, callback_key, callback)
        self._added_watch_resubscribe_required = self._needs_added_watch_resubscribe()

    async def _resubscribe_added_watchers_after_reconnect(self) -> None:
        if self._added_watch_resubscribe_required:
            modem_path = await self.find_modem()
            await self._subscribe_missing_added_watchers(modem_path)

    def _needs_added_watch_resubscribe(self) -> bool:
        if not self._added_callbacks:
            return False
        if self._modem_path is None:
            return True
        return any(
            (self._modem_path, callback_key) not in self._added_watch_keys
            for callback_key in self._added_callbacks
        )

    def _require_bus(self) -> MessageBus:
        if self._bus is None:
            raise ModemManagerUnavailable("not connected to system D-Bus")
        return self._bus

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = self._normalize_timestamp_offset(value.replace("Z", "+00:00"))
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _normalize_timestamp_offset(self, value: str) -> str:
        normalized = value
        if len(value) >= 5 and value[-5] in {"+", "-"}:
            offset = value[-4:]
            if offset.isdecimal():
                normalized = f"{value[:-5]}{value[-5]}{offset[:2]}:{offset[2:]}"
        return normalized

    def _message_sort_key(self, message: IncomingSms) -> tuple[int, float, str]:
        if message.timestamp is not None:
            return (0, message.timestamp.timestamp(), message.object_path)

        suffix = message.object_path.rsplit("/", maxsplit=1)[-1]
        path_index = int(suffix) if suffix.isdecimal() else -1
        return (1, float(path_index), message.object_path)

    def _decode_pdu_type(self, value: int | str) -> str:
        if isinstance(value, str):
            return value
        return SMS_PDU_TYPES.get(value, "unknown")

    def _decode_modem_state(self, value: int | str) -> str:
        if isinstance(value, str):
            return value
        return MODEM_STATES.get(value, "unknown")

    async def _ensure_modem_path(self) -> str:
        if self._modem_path is None:
            await self.find_modem()
        modem_path = self._modem_path
        assert modem_path is not None
        if self._added_watch_resubscribe_required:
            await self._subscribe_missing_added_watchers(modem_path)
        return modem_path

    async def _get_sim_interface(self, sim_path: str) -> SimInterface:
        _, proxy = await self._get_proxy_object("SIM object", sim_path)
        return cast(
            SimInterface,
            self._get_proxy_interface(proxy, "SIM object", sim_path, SIM_INTERFACE),
        )

    async def _get_modem_interface(
        self,
        interface_name: str,
        *,
        object_kind: str = "modem object",
    ) -> tuple[str, object]:
        modem_path, interfaces = await self._get_modem_interfaces((interface_name,), object_kind)
        return modem_path, interfaces[0]

    async def _get_modem_interfaces(
        self,
        interface_names: tuple[str, ...],
        object_kind: str = "modem object",
    ) -> tuple[str, tuple[object, ...]]:
        modem_path = await self._ensure_modem_path()
        requested_modem_path = modem_path
        modem_path, proxy = await self._get_proxy_object(
            object_kind,
            modem_path,
            refresh_cached_modem=True,
            resubscribe_added_watchers=False,
        )
        resolved_path, interfaces = await self._get_modem_interfaces_from_proxy(
            interface_names,
            object_kind,
            modem_path,
            proxy,
        )
        if resolved_path != requested_modem_path:
            await self._resubscribe_added_watchers(resolved_path)
        return resolved_path, interfaces

    async def _get_modem_interfaces_from_proxy(
        self,
        interface_names: tuple[str, ...],
        object_kind: str,
        modem_path: str,
        proxy: ProxyObject,
    ) -> tuple[str, tuple[object, ...]]:
        interfaces: list[object] = []
        failing_interface_name = ""
        try:
            for interface_name in interface_names:
                failing_interface_name = interface_name
                interfaces.append(
                    self._get_proxy_interface(proxy, object_kind, modem_path, interface_name)
                )
        except ModemManagerUnavailable as exc:
            if not self._is_stale_modem_path_unavailable(exc):
                raise
            if modem_path != self._modem_path:
                raise
            logger.info(
                "modem_interface_stale",
                modem_path=modem_path,
                interface_name=failing_interface_name,
            )
            self._modem_path = None
            refreshed_path = await self.find_modem()
            refreshed_path, proxy = await self._get_proxy_object(object_kind, refreshed_path)
            refreshed_interfaces = self._get_modem_interfaces_from_fresh_proxy(
                interface_names,
                object_kind,
                refreshed_path,
                proxy,
            )
            return refreshed_interfaces
        return modem_path, tuple(interfaces)

    def _get_modem_interfaces_from_fresh_proxy(
        self,
        interface_names: tuple[str, ...],
        object_kind: str,
        modem_path: str,
        proxy: ProxyObject,
    ) -> tuple[str, tuple[object, ...]]:
        return (
            modem_path,
            tuple(
                self._get_proxy_interface(proxy, object_kind, modem_path, interface_name)
                for interface_name in interface_names
            ),
        )

    async def _get_messaging_interface(
        self,
        modem_path: str,
        *,
        resubscribe_added_watchers: bool = True,
    ) -> tuple[str, MessagingInterface]:
        requested_modem_path = modem_path
        modem_path, proxy = await self._get_proxy_object(
            "messaging object",
            modem_path,
            refresh_cached_modem=True,
            resubscribe_added_watchers=False,
        )
        try:
            messaging = self._get_proxy_interface(
                proxy,
                "messaging object",
                modem_path,
                MESSAGING_INTERFACE,
            )
        except ModemManagerUnavailable as exc:
            if not self._is_stale_modem_path_unavailable(exc):
                raise
            if modem_path != self._modem_path:
                raise
            logger.info(
                "modem_interface_stale",
                modem_path=modem_path,
                interface_name=MESSAGING_INTERFACE,
            )
            self._modem_path = None
            refreshed_path = await self.find_modem()
            refreshed_path, proxy = await self._get_proxy_object("messaging object", refreshed_path)
            messaging = self._get_proxy_interface(
                proxy,
                "messaging object",
                refreshed_path,
                MESSAGING_INTERFACE,
            )
            if resubscribe_added_watchers:
                await self._resubscribe_added_watchers(refreshed_path)
            return refreshed_path, cast(MessagingInterface, messaging)
        if modem_path != requested_modem_path and resubscribe_added_watchers:
            await self._resubscribe_added_watchers(modem_path)
        return modem_path, cast(
            MessagingInterface,
            messaging,
        )

    async def _get_sms_interface(self, sms_path: str) -> SmsInterface:
        _, proxy = await self._get_proxy_object("SMS object", sms_path)
        return cast(
            SmsInterface,
            self._get_proxy_interface(proxy, "SMS object", sms_path, SMS_INTERFACE),
        )

    async def _get_dbus_properties_interface(self, object_path: str) -> DBusPropertiesInterface:
        _, proxy = await self._get_proxy_object("D-Bus properties object", object_path)
        return cast(
            DBusPropertiesInterface,
            self._get_proxy_interface(
                proxy,
                "D-Bus properties object",
                object_path,
                DBUS_PROPERTIES_INTERFACE,
            ),
        )

    async def _wait_for_sms_text(
        self,
        sms_path: str,
        sms: SmsInterface,
        text_changed: asyncio.Event,
        timeout_seconds: float,
    ) -> str:
        # Smaller polls recover no-signal SMS faster; larger polls reduce D-Bus traffic.
        poll_interval = 0.5
        deadline = time.monotonic() + timeout_seconds
        text = ""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(text_changed.wait(), timeout=min(poll_interval, remaining))
            text_changed.clear()
            text = await self._read_required("Text", sms.get_text)
            if text != "":
                return text

        if text == "":
            logger.warning(
                "sms_text_wait_timeout",
                sms_path=sms_path,
                timeout_seconds=timeout_seconds,
            )
        return text

    async def _get_proxy_object(
        self,
        object_kind: str,
        object_path: str,
        *,
        refresh_cached_modem: bool = False,
        resubscribe_added_watchers: bool = True,
    ) -> tuple[str, ProxyObject]:
        try:
            return object_path, await self._get_proxy_object_once(object_path)
        except DBUS_OPERATION_ERRORS as exc:
            if (
                refresh_cached_modem
                and object_path == self._modem_path
                and self._is_unknown_object_error(exc)
            ):
                logger.info("modem_path_stale", modem_path=object_path)
                self._modem_path = None
                refreshed_path = await self.find_modem()
                if resubscribe_added_watchers:
                    await self._resubscribe_added_watchers(refreshed_path)
                return await self._get_proxy_object(object_kind, refreshed_path)
            raise ModemManagerUnavailable(f"failed to query {object_kind} {object_path}") from exc

    async def _get_proxy_object_once(self, object_path: str) -> ProxyObject:
        bus = self._require_bus()
        introspection = await bus.introspect(MODEM_MANAGER_BUS_NAME, object_path)
        return cast(
            ProxyObject,
            bus.get_proxy_object(MODEM_MANAGER_BUS_NAME, object_path, introspection),
        )

    def _is_unknown_object_error(self, exc: BaseException) -> bool:
        return isinstance(exc, DBusError) and exc.type == UNKNOWN_OBJECT_ERROR

    def _is_stale_modem_path_error(self, exc: BaseException) -> bool:
        if isinstance(exc, InterfaceNotFoundError):
            return True
        return self._is_unknown_object_error(exc)

    def _is_unknown_object_unavailable(self, exc: ModemManagerUnavailable) -> bool:
        return exc.__cause__ is not None and self._is_unknown_object_error(exc.__cause__)

    def _is_stale_modem_path_unavailable(self, exc: ModemManagerUnavailable) -> bool:
        return exc.__cause__ is not None and self._is_stale_modem_path_error(exc.__cause__)

    def _get_proxy_interface(
        self,
        proxy: ProxyObject,
        object_kind: str,
        object_path: str,
        interface_name: str,
    ) -> object:
        try:
            return proxy.get_interface(interface_name)
        except DBUS_OPERATION_ERRORS as exc:
            raise ModemManagerUnavailable(
                f"failed to query {object_kind} interface {interface_name} on {object_path}"
            ) from exc

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
        property_name: str,
        reader: Callable[[], Awaitable[str]],
    ) -> str | None:
        try:
            return await reader()
        except DBUS_OPERATION_ERRORS as exc:
            if isinstance(exc, DBusError) and exc.type == UNKNOWN_PROPERTY_ERROR:
                return None
            raise ModemManagerUnavailable(
                f"failed to read optional modem property {property_name}"
            ) from exc
