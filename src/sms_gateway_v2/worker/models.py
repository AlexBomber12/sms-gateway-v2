from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DeliveryResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    outcome: Literal["sent", "failed_permanent", "retry_scheduled"]
    item_id: str = Field(min_length=1)
    attempts_used: int = Field(ge=0)
    reason: str | None = None
    next_retry_at: datetime | None = None
