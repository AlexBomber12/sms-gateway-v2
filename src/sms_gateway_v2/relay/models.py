from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

RelayStatus = Literal["stopped", "starting", "running", "stopping"]


class RelayState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: RelayStatus
    started_at: datetime | None
    last_sms_received_at: datetime | None
    last_error: str | None
    sms_delete_failures_count: int = 0
    last_delete_failure_at: datetime | None = None
