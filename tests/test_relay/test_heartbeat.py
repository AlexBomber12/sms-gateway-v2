from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.relay import HeartbeatScheduler, SmsRelay, build_relay
from sms_gateway_v2.relay import heartbeat as heartbeat_module
from sms_gateway_v2.relay.models import RelayState, RelayStatus
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.telegram.exceptions import TelegramError
from sms_gateway_v2.telegram.models import TelegramMessage


def _make_state(
    *,
    status: RelayStatus = "running",
    started_at: datetime | None = datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
    last_sms_received_at: datetime | None = datetime(2026, 4, 28, 14, 22, 11, tzinfo=UTC),
    last_error: str | None = None,
    modem_state: str | None = "registered",
    modem_signal_percent: int | None = 86,
    modem_operator: str | None = "I TIM",
    modem_registration: str | None = "roaming",
    queue_pending_count: int | None = 0,
    queue_failed_count: int | None = 0,
    sms_delete_failures_count: int = 0,
    last_telegram_success_at: datetime | None = None,
) -> RelayState:
    return RelayState(
        status=status,
        started_at=started_at,
        last_sms_received_at=last_sms_received_at,
        last_error=last_error,
        sms_delete_failures_count=sms_delete_failures_count,
        modem_state=modem_state,
        modem_signal_percent=modem_signal_percent,
        modem_operator=modem_operator,
        modem_registration=modem_registration,
        queue_pending_count=queue_pending_count,
        queue_failed_count=queue_failed_count,
        last_telegram_success_at=last_telegram_success_at,
    )


def _make_relay(**state_overrides: Any) -> MagicMock:
    relay = MagicMock(spec=SmsRelay)
    relay.state = MagicMock(return_value=_make_state(**state_overrides))
    return relay


def _make_telegram_client() -> MagicMock:
    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.send_message = AsyncMock()
    return telegram_client


def _render_heartbeat_text(**state_overrides: Any) -> str:
    return heartbeat_module._format_heartbeat_text(_make_state(**state_overrides))


async def _send_heartbeat_once(relay: MagicMock, *, chat_id: str = "-100") -> TelegramMessage:
    telegram_client = _make_telegram_client()
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id=chat_id,
        interval_seconds=86400.0,
    )

    await scheduler._send_heartbeat()

    telegram_client.send_message.assert_awaited_once()
    sent_message = telegram_client.send_message.await_args.args[0]
    assert isinstance(sent_message, TelegramMessage)
    return sent_message


async def test_send_heartbeat_sends_message_with_current_state() -> None:
    sent_message = await _send_heartbeat_once(
        _make_relay(last_error="something"),
        chat_id="-100200300",
    )

    assert sent_message.chat_id == "-100200300"
    assert sent_message.text.splitlines()[0] == "<b>✅ SMS Gateway</b>"
    assert "Error: something" in sent_message.text


async def test_send_heartbeat_escapes_html_in_last_error() -> None:
    sent_message = await _send_heartbeat_once(
        _make_relay(last_error="<b>boom</b> & oops"),
    )

    assert "Error: &lt;b&gt;boom&lt;/b&gt; &amp; oops" in sent_message.text
    assert "<b>boom</b>" not in sent_message.text


async def test_heartbeat_healthy_body_is_three_lines() -> None:
    timestamp = datetime.now(UTC) - timedelta(days=3)

    sent_message = await _send_heartbeat_once(
        _make_relay(started_at=timestamp, last_sms_received_at=timestamp),
    )

    assert sent_message.text.splitlines() == [
        "<b>✅ SMS Gateway</b>",
        "Modem: registered · 86% · I TIM (roaming)",
        "SMS 3 days ago · uptime 3 days",
    ]


def test_heartbeat_healthy_title_and_emoji() -> None:
    text = _render_heartbeat_text()

    assert text.splitlines()[0] == "<b>✅ SMS Gateway</b>"


async def test_heartbeat_healthy_is_silent() -> None:
    sent_message = await _send_heartbeat_once(_make_relay())

    assert sent_message.disable_notification is True


async def test_heartbeat_degraded_is_audible() -> None:
    sent_message = await _send_heartbeat_once(_make_relay(modem_state="disabled"))

    assert sent_message.disable_notification is False


def test_heartbeat_degraded_title_names_primary_reason() -> None:
    text = _render_heartbeat_text(modem_state="disabled")

    assert text.splitlines()[0] == "<b>🔴 SMS Gateway: modem disabled</b>"


def test_heartbeat_degraded_title_counts_extra_reasons() -> None:
    text = _render_heartbeat_text(modem_state="disabled", sms_delete_failures_count=2)

    assert text.splitlines()[0] == "<b>🔴 SMS Gateway: modem disabled +1</b>"


def test_heartbeat_title_uses_delete_failures_when_modem_healthy() -> None:
    text = _render_heartbeat_text(modem_state="registered", sms_delete_failures_count=2)

    assert text.splitlines()[0] == "<b>🔴 SMS Gateway: 2 delete failures</b>"


def test_heartbeat_hides_zero_queue_and_delete_lines() -> None:
    text = _render_heartbeat_text(queue_pending_count=0, queue_failed_count=0)

    assert "Queue:" not in text
    assert "Delete failures:" not in text


@pytest.mark.parametrize(
    ("pending_count", "failed_count", "expected_line"),
    [
        (3, 1, "Queue: 3 pending, 1 failed"),
        (None, 1, "Queue: unknown pending, 1 failed"),
        (3, None, "Queue: 3 pending, unknown failed"),
    ],
)
def test_heartbeat_shows_queue_line_when_non_zero(
    pending_count: int | None,
    failed_count: int | None,
    expected_line: str,
) -> None:
    text = _render_heartbeat_text(
        queue_pending_count=pending_count,
        queue_failed_count=failed_count,
    )

    assert expected_line in text.splitlines()
    assert text.splitlines()[0] == "<b>✅ SMS Gateway</b>"


@pytest.mark.parametrize(
    ("last_error", "expected_line"),
    [
        (None, None),
        ("<b>boom</b> & oops", "Error: &lt;b&gt;boom&lt;/b&gt; &amp; oops"),
    ],
)
def test_heartbeat_shows_error_line_only_when_present(
    last_error: str | None,
    expected_line: str | None,
) -> None:
    text = _render_heartbeat_text(last_error=last_error)
    error_lines = [line for line in text.splitlines() if line.startswith("Error:")]

    if expected_line is None:
        assert error_lines == []
    else:
        assert error_lines == [expected_line]


@pytest.mark.parametrize(
    ("state_overrides", "expected_line"),
    [
        (
            {},
            "Modem: registered · 86% · I TIM (roaming)",
        ),
        (
            {
                "modem_state": "disabled",
                "modem_signal_percent": 0,
                "modem_operator": None,
                "modem_registration": None,
            },
            "Modem: disabled · 0%",
        ),
        (
            {
                "modem_state": None,
                "modem_signal_percent": None,
                "modem_operator": None,
                "modem_registration": None,
            },
            "Modem: unknown",
        ),
    ],
)
def test_heartbeat_modem_line_variants(
    state_overrides: dict[str, object],
    expected_line: str,
) -> None:
    text = _render_heartbeat_text(**state_overrides)
    modem_line = next(line for line in text.splitlines() if line.startswith("Modem:"))

    assert modem_line == expected_line


def test_heartbeat_ages_line_handles_missing_values() -> None:
    text = _render_heartbeat_text(started_at=None, last_sms_received_at=None)

    assert text.splitlines()[-1] == "SMS never · uptime unknown"


def test_heartbeat_body_contains_no_iso_timestamps() -> None:
    text = _render_heartbeat_text(
        started_at=datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
        last_sms_received_at=datetime(2026, 4, 28, 14, 22, 11, tzinfo=UTC),
        last_telegram_success_at=datetime(2026, 5, 1, 18, 5, 0, tzinfo=UTC),
    )

    assert re.search(r"\d{4}-\d{2}-\d{2}T", text) is None
    assert "Status: running" not in text
    assert "Last Telegram OK:" not in text


async def test_heartbeat_sent_log_includes_degraded_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    log_events: list[tuple[str, dict[str, Any]]] = []

    class CapturingLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(heartbeat_module, "logger", CapturingLogger())

    await _send_heartbeat_once(_make_relay(modem_state="registered"))
    await _send_heartbeat_once(_make_relay(modem_state="disabled"))

    sent_events = [kwargs for event, kwargs in log_events if event == "heartbeat_sent"]
    assert sent_events == [
        {"chat_id": "-100", "degraded": False},
        {"chat_id": "-100", "degraded": True},
    ]


async def test_heartbeat_stays_alive_when_no_recent_delivery() -> None:
    stale_telegram_ok = datetime.now(UTC) - timedelta(days=30)
    sent_message = await _send_heartbeat_once(
        _make_relay(
            last_sms_received_at=datetime.now(UTC) - timedelta(days=40),
            last_telegram_success_at=stale_telegram_ok,
        )
    )

    # Regression for the 2026-08-26 false alarm: quiet SMS traffic is expected here.
    assert sent_message.text.splitlines()[0] == "<b>✅ SMS Gateway</b>"
    assert "ALERT" not in sent_message.text
    assert "Last Telegram OK:" not in sent_message.text
    assert sent_message.disable_notification is True


@pytest.mark.parametrize(
    "delivery_age",
    [
        timedelta(hours=1),
        timedelta(hours=49),
        timedelta(days=40),
    ],
)
def test_heartbeat_degraded_reasons_do_not_include_delivery_age(
    delivery_age: timedelta,
) -> None:
    state = _make_state(last_telegram_success_at=datetime.now(UTC) - delivery_age)

    assert heartbeat_module._degradation_reasons(state) == []


def test_heartbeat_degradation_reasons_include_modem_state() -> None:
    state = _make_state(modem_state="disabled", sms_delete_failures_count=0)

    assert heartbeat_module._degradation_reasons(state) == [
        "- modem state: disabled (expected registered)"
    ]


def test_heartbeat_degradation_reasons_include_delete_failures() -> None:
    state = _make_state(modem_state="registered", sms_delete_failures_count=3)

    assert heartbeat_module._degradation_reasons(state) == ["- delete failures: 3"]


def test_heartbeat_degraded_reasons_show_multiple_reasons() -> None:
    state = _make_state(
        modem_state="disabled",
        sms_delete_failures_count=3,
        last_telegram_success_at=datetime.now(UTC) - timedelta(hours=50),
    )

    assert heartbeat_module._degradation_reasons(state) == [
        "- modem state: disabled (expected registered)",
        "- delete failures: 3",
    ]


async def test_send_heartbeat_swallows_validation_error_from_oversized_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay(last_error="x" * 5000)
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=86400.0,
    )

    log_events: list[tuple[str, dict[str, Any]]] = []

    class CapturingLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(heartbeat_module, "logger", CapturingLogger())

    await scheduler._send_heartbeat()

    telegram_client.send_message.assert_not_awaited()
    assert any(event == "heartbeat_send_failed" for event, _ in log_events)


async def test_run_keeps_looping_after_validation_error() -> None:
    telegram_client = _make_telegram_client()
    states = [
        RelayState(
            status="running",
            started_at=datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
            last_sms_received_at=None,
            last_error="x" * 5000,
        ),
        RelayState(
            status="running",
            started_at=datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
            last_sms_received_at=None,
            last_error=None,
        ),
    ]
    relay = MagicMock(spec=SmsRelay)
    relay.state = MagicMock(side_effect=states)
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=0.01,
    )

    sent_calls = 0
    original_send = scheduler._send_heartbeat

    async def counting_send() -> None:
        nonlocal sent_calls
        sent_calls += 1
        await original_send()
        if sent_calls >= 2:
            scheduler.stop()

    scheduler._send_heartbeat = counting_send  # type: ignore[method-assign]

    await asyncio.wait_for(scheduler.run(), timeout=1.0)

    assert sent_calls == 2
    telegram_client.send_message.assert_awaited_once()


async def test_send_heartbeat_swallows_telegram_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_client = _make_telegram_client()
    telegram_client.send_message = AsyncMock(side_effect=TelegramError("boom"))
    relay = _make_relay()
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=86400.0,
    )

    log_events: list[tuple[str, dict[str, Any]]] = []

    class CapturingLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(heartbeat_module, "logger", CapturingLogger())

    await scheduler._send_heartbeat()

    assert any(event == "heartbeat_send_failed" for event, _ in log_events)


async def test_run_does_not_send_on_first_iteration_when_stop_set_during_wait() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay()
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=3600.0,
    )

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        scheduler.stop()

    await asyncio.gather(scheduler.run(), stop_soon())

    telegram_client.send_message.assert_not_awaited()


async def test_run_loops_until_stop_called() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay()
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=0.01,
    )

    sent_calls = 0

    async def stopping_send() -> None:
        nonlocal sent_calls
        sent_calls += 1
        scheduler.stop()

    scheduler._send_heartbeat = stopping_send  # type: ignore[method-assign]

    await asyncio.wait_for(scheduler.run(), timeout=1.0)

    assert sent_calls == 1


async def test_run_keeps_looping_after_send_succeeds() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay()
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=0.01,
    )

    sent_calls = 0

    async def counting_send() -> None:
        nonlocal sent_calls
        sent_calls += 1
        if sent_calls >= 3:
            scheduler.stop()

    scheduler._send_heartbeat = counting_send  # type: ignore[method-assign]

    await asyncio.wait_for(scheduler.run(), timeout=1.0)

    assert sent_calls == 3


def test_factory_uses_main_chat_when_heartbeat_chat_id_empty(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        heartbeat_enabled=True,
        heartbeat_telegram_chat_id="",
    )
    metrics = MetricsRegistry()

    _, _, _, _, heartbeat_scheduler = build_relay(settings, metrics)

    assert heartbeat_scheduler is not None
    assert heartbeat_scheduler._chat_id == "-100200300"


def test_factory_uses_dedicated_chat_when_heartbeat_chat_id_set(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        heartbeat_enabled=True,
        heartbeat_telegram_chat_id="-555444333",
        heartbeat_interval_seconds=120.0,
    )
    metrics = MetricsRegistry()

    _, _, _, _, heartbeat_scheduler = build_relay(settings, metrics)

    assert heartbeat_scheduler is not None
    assert heartbeat_scheduler._chat_id == "-555444333"
    assert heartbeat_scheduler._interval_seconds == 120.0


def test_factory_returns_none_heartbeat_when_disabled(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path / "state",
        telegram_bot_token="test-token",
        telegram_chat_id="-100200300",
        heartbeat_enabled=False,
    )
    metrics = MetricsRegistry()

    _, _, _, _, heartbeat_scheduler = build_relay(settings, metrics)

    assert heartbeat_scheduler is None
