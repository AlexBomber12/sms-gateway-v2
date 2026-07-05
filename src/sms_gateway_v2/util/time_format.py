from __future__ import annotations

from datetime import UTC, datetime

_SECONDS_PER_UNIT = (
    ("day", 86_400),
    ("hour", 3_600),
    ("minute", 60),
    ("second", 1),
)


def format_duration_since(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "(none)"

    now = datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    total_seconds = max(0, int((now - timestamp).total_seconds()))

    parts: list[str] = []
    remaining = total_seconds
    for unit, unit_seconds in _SECONDS_PER_UNIT:
        value, remaining = divmod(remaining, unit_seconds)
        if value == 0:
            continue
        suffix = "" if value == 1 else "s"
        parts.append(f"{value} {unit}{suffix}")
        if len(parts) == 2:
            break

    if not parts:
        return "0 seconds"
    return " ".join(parts)
