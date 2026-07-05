from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sms_gateway_v2.util.time_format import format_duration_since


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(days=5, hours=2, minutes=40), "5 days 2 hours"),
        (timedelta(minutes=3, seconds=12), "3 minutes 12 seconds"),
        (timedelta(seconds=47), "47 seconds"),
        (timedelta(seconds=0), "0 seconds"),
    ],
)
def test_format_duration_since_returns_two_units(delta: timedelta, expected: str) -> None:
    timestamp = datetime.now(UTC) - delta

    assert format_duration_since(timestamp) == expected


def test_format_duration_since_handles_none_returns_placeholder() -> None:
    assert format_duration_since(None) == "(none)"


def test_format_duration_since_handles_naive_timestamp() -> None:
    timestamp = datetime.now() - timedelta(seconds=47)

    assert format_duration_since(timestamp) == "47 seconds"
