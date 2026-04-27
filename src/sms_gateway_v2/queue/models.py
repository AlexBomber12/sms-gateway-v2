from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue.exceptions import QueueCorrupted


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class QueueItem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    sms: IncomingSms
    first_seen_at: datetime
    content_hash: str | None = None
    attempts: int = 0
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        return value

    @classmethod
    def new(cls, sms: IncomingSms) -> QueueItem:
        timestamp_ms = int(time.time() * 1000)
        return cls(
            id=f"{timestamp_ms}-{uuid4().hex}",
            sms=sms,
            first_seen_at=datetime.now(UTC),
        )

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, payload: str) -> QueueItem:
        try:
            return cls.model_validate_json(payload)
        except ValidationError as exc:
            raise QueueCorrupted(f"invalid queue item JSON: {exc}") from exc
