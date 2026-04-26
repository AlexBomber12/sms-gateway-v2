from __future__ import annotations

import hashlib
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

    @classmethod
    def from_dbus_value(cls, value: int) -> RegistrationState:
        states = {
            0: cls.IDLE,
            1: cls.HOME,
            2: cls.SEARCHING,
            3: cls.DENIED,
            4: cls.UNKNOWN,
            5: cls.ROAMING,
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

    def content_hash(self) -> str:
        if self.timestamp is None:
            minute_iso = ""
        else:
            minute_iso = self.timestamp.replace(second=0, microsecond=0).isoformat()
        payload = f"{self.number}|{self.text}|{minute_iso}"
        return hashlib.sha256(payload.encode()).hexdigest()
