from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegistrationState(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    SEARCHING = "searching"
    DENIED = "denied"
    HOME = "home"
    ROAMING = "roaming"
    HOME_SMS_ONLY = "home_sms_only"
    ROAMING_SMS_ONLY = "roaming_sms_only"
    EMERGENCY_ONLY = "emergency_only"
    HOME_CSFB_NOT_PREFERRED = "home_csfb_not_preferred"
    ROAMING_CSFB_NOT_PREFERRED = "roaming_csfb_not_preferred"
    ATTACHED_RLOS = "attached_rlos"

    @classmethod
    def from_dbus_value(cls, value: int) -> RegistrationState:
        states = {
            0: cls.IDLE,
            1: cls.HOME,
            2: cls.SEARCHING,
            3: cls.DENIED,
            4: cls.UNKNOWN,
            5: cls.ROAMING,
            6: cls.HOME_SMS_ONLY,
            7: cls.ROAMING_SMS_ONLY,
            8: cls.EMERGENCY_ONLY,
            9: cls.HOME_CSFB_NOT_PREFERRED,
            10: cls.ROAMING_CSFB_NOT_PREFERRED,
            11: cls.ATTACHED_RLOS,
        }
        return states.get(value, cls.UNKNOWN)


class SignalQuality(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    percent: int
    recent: bool
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("percent")
    @classmethod
    def validate_percent(cls, value: int) -> int:
        if not 0 <= value <= 100:
            msg = "percent must be between 0 and 100"
            raise ValueError(msg)
        return value


class ModemInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    object_path: str
    manufacturer: str
    model: str
    equipment_id: str
    device: str
    state: str
    registration: RegistrationState
    signal: SignalQuality | None
    sim_imsi: str | None
    sim_operator_name: str | None
    sim_operator_id: str | None


class IncomingSms(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    object_path: str
    number: str
    text: str
    timestamp: datetime | None
    pdu_type: str
