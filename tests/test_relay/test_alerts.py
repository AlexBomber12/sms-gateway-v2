from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sms_gateway_v2.config import Settings
from sms_gateway_v2.metrics import MetricsRegistry
from sms_gateway_v2.relay.alerts import (
    RUNBOOK,
    AlertOnlyScheduler,
    _Incident,
    _reason,
    _text,
)
from sms_gateway_v2.relay.factory import build_relay
from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.telegram.exceptions import TelegramError, TelegramRateLimited


def state(**changes: object) -> RelayState:
    values: dict[str, object] = {
        "status": "running",
        "started_at": None,
        "last_sms_received_at": None,
        "last_error": None,
        "modem_state": "registered",
        "queue_failed_count": 0,
        "queue_pending_count": 0,
    }
    values.update(changes)
    return RelayState.model_validate(values)


def scheduler(path: Path) -> AlertOnlyScheduler:
    relay = MagicMock()
    relay.state.return_value = state()
    client = MagicMock()
    client.send_message = AsyncMock()
    return AlertOnlyScheduler(client, relay, "test-chat", path)


async def poll(s: AlertOnlyScheduler, now: float) -> None:
    with patch("sms_gateway_v2.relay.alerts.time.time", return_value=now):
        await s._send_heartbeat()


async def bad_for(s: AlertOnlyScheduler, start: int = 1000, seconds: int = 900) -> int:
    for now in range(start, start + seconds + 1, 60):
        await poll(s, now)
    return start + seconds


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, None),
        ({"status": "stopped"}, "relay"),
        ({"modem_state": None}, "modem"),
        ({"modem_state": "unavailable"}, "modem"),
        ({"queue_failed_count": None}, "unknown"),
        ({"queue_pending_count": None}, "unknown"),
        ({"queue_failed_count": 1}, "delivery"),
        ({"queue_pending_count": 1}, "pending"),
    ],
)
def test_conditions(changes: dict[str, object], expected: str | None) -> None:
    assert _reason(state(**changes)) == expected


async def test_quiet_healthy_and_transient_failure(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    await bad_for(s)
    s._relay.state.return_value = state(modem_state="failed")
    await bad_for(s, 1960, 840)
    s._relay.state.return_value = state()
    await bad_for(s, 2860)
    s._telegram_client.send_message.assert_not_awaited()


async def test_persistent_failure_one_alert_restart_dedup_and_one_recovery(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    s._relay.state.return_value = state(modem_state="failed", last_error="PRIVATE_SMS_TOKEN")
    await bad_for(s)
    s._telegram_client.send_message.assert_awaited_once()
    message = s._telegram_client.send_message.call_args.args[0]
    assert "PRIVATE_SMS_TOKEN" not in message.text
    assert RUNBOOK in message.text and message.parse_mode is None
    restarted = scheduler(tmp_path)
    restarted._relay.state.return_value = state(modem_state="failed")
    await bad_for(restarted, 1960)
    restarted._telegram_client.send_message.assert_not_awaited()
    restarted._relay.state.return_value = state()
    await poll(restarted, 2920)
    await poll(restarted, 2980)
    restarted._telegram_client.send_message.assert_not_awaited()
    await poll(restarted, 3040)
    restarted._telegram_client.send_message.assert_awaited_once()
    assert restarted._telegram_client.send_message.call_args.args[0].text == _text(None)
    await bad_for(restarted, 3100)
    restarted._telegram_client.send_message.assert_awaited_once()


async def test_daily_reminder_only_for_unresolved_incident(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    s._relay.state.return_value = state(queue_failed_count=1)
    await bad_for(s)
    for now in range(1960, 1900 + 86400, 60):
        await poll(s, now)
    assert s._telegram_client.send_message.await_count == 1
    await poll(s, 1900 + 86400)
    assert s._telegram_client.send_message.await_count == 2


async def test_progress_prevents_busy_queue_false_alert(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    for now in range(1000, 3000, 60):
        s._relay.state.return_value = state(
            queue_pending_count=1, last_telegram_success_at=datetime.fromtimestamp(now, UTC)
        )
        await poll(s, now)
    s._telegram_client.send_message.assert_not_awaited()
    await bad_for(s, 3040)
    s._telegram_client.send_message.assert_awaited_once()


@pytest.mark.parametrize(
    "exception",
    [TelegramError("PRIVATE_TOKEN"), TelegramRateLimited("PRIVATE_TOKEN", retry_after=3600)],
)
async def test_send_failure_backoff_and_retry(tmp_path: Path, exception: TelegramError) -> None:
    s = scheduler(tmp_path)
    s._relay.state.return_value = state(modem_state="failed")
    s._telegram_client.send_message.side_effect = exception
    await bad_for(s)
    await poll(s, 1960)
    assert s._telegram_client.send_message.await_count == 1
    s._telegram_client.send_message.side_effect = None
    for now in range(2020, 5560, 60):
        await poll(s, now)
    assert s._telegram_client.send_message.await_count == 2


async def test_observation_gap_does_not_prove_persistence(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    s._relay.state.return_value = state(modem_state="failed")
    await poll(s, 1000)
    await poll(s, 10000)
    await poll(s, 9999)
    s._telegram_client.send_message.assert_not_awaited()


async def test_no_stale_message_when_health_changed(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    s._relay.state.return_value = state(modem_state="failed")
    await bad_for(s, seconds=840)
    s._relay.state.side_effect = [state(modem_state="failed"), state()]
    await poll(s, 1900)
    s._telegram_client.send_message.assert_not_awaited()


@pytest.mark.parametrize("exception", [OSError("PRIVATE_PATH"), sqlite3.OperationalError("SECRET")])
async def test_observer_failure_does_not_stop_relay(tmp_path: Path, exception: Exception) -> None:
    s = scheduler(tmp_path)
    with patch.object(s, "_claim", side_effect=exception):
        await poll(s, 1000)
    s._telegram_client.send_message.assert_not_awaited()


async def test_corrupt_state_fails_quietly(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    await poll(s, 1000)
    with sqlite3.connect(s._path) as db:
        db.execute("UPDATE incident SET state='invalid' WHERE id=1")
    await poll(s, 1060)
    s._telegram_client.send_message.assert_not_awaited()


def test_factory_opt_in_keeps_existing_destination_and_separate_database(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token="test",
        telegram_chat_id="sms-destination",
        heartbeat_telegram_chat_id="infrastructure-destination",
        heartbeat_alert_only=True,
        state_dir=tmp_path,
    )
    _, _, _, _, observer = build_relay(settings, MetricsRegistry())
    assert isinstance(observer, AlertOnlyScheduler)
    assert observer._chat_id == "infrastructure-destination"
    assert observer._interval_seconds == 60
    assert observer._path == tmp_path / "notification-incidents.sqlite3"


def test_claim_persists_attempt_lease(tmp_path: Path) -> None:
    s = scheduler(tmp_path)
    for now in range(1000, 1901, 60):
        result = s._claim(now, "modem", None)
    assert result is not None and result.next_attempt == 2200
    assert s._claim(1960, "modem", None) is None
    assert _Incident().sent_at is None
