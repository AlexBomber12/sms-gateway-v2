from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sms_gateway_v2.worker import DeliveryResult


def test_delivery_result_accepts_valid_outcomes() -> None:
    next_retry_at = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)

    result = DeliveryResult(
        outcome="retry_scheduled",
        item_id="item-1",
        attempts_used=1,
        reason="transport_error",
        next_retry_at=next_retry_at,
    )

    assert result.outcome == "retry_scheduled"
    assert result.item_id == "item-1"
    assert result.attempts_used == 1
    assert result.reason == "transport_error"
    assert result.next_retry_at == next_retry_at


@pytest.mark.parametrize("outcome", ["sent", "failed_permanent", "retry_scheduled"])
def test_delivery_result_outcome_allows_known_values(outcome: str) -> None:
    result = DeliveryResult(outcome=outcome, item_id="item-1", attempts_used=0)

    assert result.outcome == outcome


def test_delivery_result_rejects_unknown_outcome() -> None:
    with pytest.raises(ValidationError, match="outcome"):
        DeliveryResult(outcome="unknown", item_id="item-1", attempts_used=0)


def test_delivery_result_rejects_empty_item_id() -> None:
    with pytest.raises(ValidationError, match="item_id"):
        DeliveryResult(outcome="sent", item_id="", attempts_used=0)


def test_delivery_result_rejects_negative_attempts_used() -> None:
    with pytest.raises(ValidationError, match="attempts_used"):
        DeliveryResult(outcome="sent", item_id="item-1", attempts_used=-1)
