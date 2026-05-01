from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.relay import HeartbeatScheduler, SmsRelay, build_relay
from sms_gateway_v2.relay import heartbeat as heartbeat_module
from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.telegram.exceptions import TelegramError
from sms_gateway_v2.telegram.models import TelegramMessage


def _make_relay(
    *,
    status: str = "running",
    started_at: datetime | None = datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
    last_sms_received_at: datetime | None = datetime(2026, 4, 28, 14, 22, 11, tzinfo=UTC),
    last_error: str | None = None,
) -> MagicMock:
    relay = MagicMock(spec=SmsRelay)
    relay.state = MagicMock(
        return_value=RelayState(
            status=status,  # type: ignore[arg-type]
            started_at=started_at,
            last_sms_received_at=last_sms_received_at,
            last_error=last_error,
        )
    )
    return relay


def _make_telegram_client() -> MagicMock:
    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.send_message = AsyncMock()
    return telegram_client


async def test_send_heartbeat_sends_message_with_current_state() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay(last_error="something")
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100200300",
        interval_seconds=86400.0,
    )

    await scheduler._send_heartbeat()

    telegram_client.send_message.assert_awaited_once()
    sent_message = telegram_client.send_message.await_args.args[0]
    assert isinstance(sent_message, TelegramMessage)
    assert sent_message.chat_id == "-100200300"
    assert "SMS Gateway v2: alive" in sent_message.text
    assert "Status: running" in sent_message.text
    assert "Started: 2026-05-01T18:00:00Z" in sent_message.text
    assert "Last SMS: 2026-04-28T14:22:11Z" in sent_message.text
    assert "Last error: something" in sent_message.text


async def test_send_heartbeat_escapes_html_in_last_error() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay(last_error="<b>boom</b> & oops")
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=86400.0,
    )

    await scheduler._send_heartbeat()

    sent_message = telegram_client.send_message.await_args.args[0]
    assert "Last error: &lt;b&gt;boom&lt;/b&gt; &amp; oops" in sent_message.text
    assert "<b>boom</b>" not in sent_message.text


async def test_send_heartbeat_renders_none_values_as_placeholder() -> None:
    telegram_client = _make_telegram_client()
    relay = _make_relay(started_at=None, last_sms_received_at=None, last_error=None)
    scheduler = HeartbeatScheduler(
        telegram_client=telegram_client,
        relay=relay,
        chat_id="-100",
        interval_seconds=86400.0,
    )

    await scheduler._send_heartbeat()

    sent_message = telegram_client.send_message.await_args.args[0]
    assert "Started: (none)" in sent_message.text
    assert "Last SMS: (none)" in sent_message.text
    assert "Last error: (none)" in sent_message.text


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
