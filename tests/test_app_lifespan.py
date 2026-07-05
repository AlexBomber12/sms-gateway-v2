from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sms_gateway_v2 import app as app_module
from sms_gateway_v2.metrics import QueueGaugeUpdater
from sms_gateway_v2.relay import (
    CleanupScheduler,
    HeartbeatScheduler,
    ModemWatchdog,
    RelayError,
    SmsRelay,
)
from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.telegram import TelegramClient


@pytest.fixture
def relay_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100200300")
    yield


def _make_gauge_updater() -> MagicMock:
    gauge_updater = MagicMock(spec=QueueGaugeUpdater)
    gauge_updater.run = AsyncMock()
    gauge_updater.stop = MagicMock()
    return gauge_updater


def _make_watchdog() -> MagicMock:
    watchdog = MagicMock(spec=ModemWatchdog)
    watchdog.run = AsyncMock()
    watchdog.stop = MagicMock()
    return watchdog


def _make_cleanup_scheduler() -> MagicMock:
    cleanup_scheduler = MagicMock(spec=CleanupScheduler)
    cleanup_scheduler.run = AsyncMock()
    cleanup_scheduler.stop = MagicMock()
    return cleanup_scheduler


def _make_heartbeat_scheduler() -> MagicMock:
    heartbeat_scheduler = MagicMock(spec=HeartbeatScheduler)
    heartbeat_scheduler.run = AsyncMock()
    heartbeat_scheduler.stop = MagicMock()
    return heartbeat_scheduler


def test_relay_disabled_skips_lifespan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "false")

    app = app_module.create_app()
    with TestClient(app) as client:
        assert not hasattr(app.state, "relay")
        response = client.get("/healthz")
    assert response.status_code == 200


def test_state_endpoint_serializes_delete_failure_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "false")

    relay = MagicMock(spec=SmsRelay)
    relay.state.return_value = RelayState(
        status="stopped",
        started_at=None,
        last_sms_received_at=None,
        last_error=None,
    )

    app = app_module.create_app()
    app.state.relay = relay
    with TestClient(app) as client:
        response = client.get("/state")

    assert response.status_code == 200
    assert response.json()["sms_delete_failures_count"] == 0
    assert response.json()["last_delete_failure_at"] is None


def test_relay_enabled_calls_start_and_stop(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setenv("HEARTBEAT_ENABLED", "true")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    call_order: list[str] = []
    relay.start.side_effect = lambda: call_order.append("relay_start")
    gauge_updater.stop.side_effect = lambda: call_order.append("gauge_stop")
    cleanup_scheduler.stop.side_effect = lambda: call_order.append("cleanup_stop")
    watchdog.stop.side_effect = lambda: call_order.append("watchdog_stop")
    heartbeat_scheduler.stop.side_effect = lambda: call_order.append("heartbeat_stop")
    relay.stop.side_effect = lambda: call_order.append("relay_stop")

    fake_build_relay = MagicMock(
        return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
    )
    monkeypatch.setattr(app_module, "build_relay", fake_build_relay)

    app = app_module.create_app()
    with TestClient(app):
        pass

    fake_build_relay.assert_called_once()
    telegram_client.__aenter__.assert_awaited_once()
    telegram_client.__aexit__.assert_awaited_once()
    relay.start.assert_awaited_once()
    relay.stop.assert_awaited_once()
    gauge_updater.run.assert_awaited_once()
    gauge_updater.stop.assert_called_once()
    cleanup_scheduler.run.assert_awaited_once()
    cleanup_scheduler.stop.assert_called_once()
    watchdog.run.assert_awaited_once()
    watchdog.stop.assert_called_once()
    heartbeat_scheduler.run.assert_awaited_once()
    heartbeat_scheduler.stop.assert_called_once()
    assert call_order == [
        "relay_start",
        "gauge_stop",
        "cleanup_stop",
        "watchdog_stop",
        "heartbeat_stop",
        "relay_stop",
    ]


def test_relay_enabled_skips_heartbeat_when_disabled(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setenv("HEARTBEAT_ENABLED", "false")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()

    fake_build_relay = MagicMock(
        return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, None)
    )
    monkeypatch.setattr(app_module, "build_relay", fake_build_relay)

    app = app_module.create_app()
    with TestClient(app) as client:
        assert app.state.heartbeat_scheduler is None
        assert app.state.heartbeat_task is None
        response = client.get("/healthz")

    assert response.status_code == 200
    relay.start.assert_awaited_once()
    relay.stop.assert_awaited_once()


def test_relay_enabled_missing_credentials_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    app = app_module.create_app()
    with pytest.raises(RelayError, match="telegram_bot_token"), TestClient(app):
        pass


def test_relay_start_failure_releases_telegram_client(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock(side_effect=RelayError("boom"))
    relay.stop = AsyncMock()

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with pytest.raises(RelayError, match="boom"), TestClient(app):
        pass

    telegram_client.__aenter__.assert_awaited_once()
    telegram_client.__aexit__.assert_awaited_once()
    relay.stop.assert_not_awaited()
    gauge_updater.run.assert_not_awaited()
    gauge_updater.stop.assert_not_called()
    cleanup_scheduler.run.assert_not_awaited()
    cleanup_scheduler.stop.assert_not_called()
    watchdog.run.assert_not_awaited()
    watchdog.stop.assert_not_called()
    heartbeat_scheduler.run.assert_not_awaited()
    heartbeat_scheduler.stop.assert_not_called()


def test_relay_shutdown_cancels_gauge_task_on_timeout(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    import asyncio as _asyncio

    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setattr(app_module, "BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    async def never_stops() -> None:
        await _asyncio.Event().wait()

    gauge_updater = MagicMock(spec=QueueGaugeUpdater)
    gauge_updater.run = AsyncMock(side_effect=never_stops)
    gauge_updater.stop = MagicMock()

    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with TestClient(app):
        pass

    gauge_updater.stop.assert_called_once()
    cleanup_scheduler.stop.assert_called_once()
    watchdog.stop.assert_called_once()
    heartbeat_scheduler.stop.assert_called_once()
    relay.stop.assert_awaited_once()


def test_relay_shutdown_cancels_cleanup_task_on_timeout(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    import asyncio as _asyncio

    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setattr(app_module, "BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    async def never_stops() -> None:
        await _asyncio.Event().wait()

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = MagicMock(spec=CleanupScheduler)
    cleanup_scheduler.run = AsyncMock(side_effect=never_stops)
    cleanup_scheduler.stop = MagicMock()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with TestClient(app):
        pass

    cleanup_scheduler.stop.assert_called_once()
    watchdog.stop.assert_called_once()
    heartbeat_scheduler.stop.assert_called_once()
    relay.stop.assert_awaited_once()


def test_relay_shutdown_cancels_watchdog_task_on_timeout(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    import asyncio as _asyncio

    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setattr(app_module, "BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    async def never_stops() -> None:
        await _asyncio.Event().wait()

    gauge_updater = _make_gauge_updater()
    watchdog = MagicMock(spec=ModemWatchdog)
    watchdog.run = AsyncMock(side_effect=never_stops)
    watchdog.stop = MagicMock()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with TestClient(app):
        pass

    watchdog.stop.assert_called_once()
    cleanup_scheduler.stop.assert_called_once()
    heartbeat_scheduler.stop.assert_called_once()
    relay.stop.assert_awaited_once()


def test_relay_shutdown_cancels_heartbeat_task_on_timeout(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    import asyncio as _asyncio

    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setattr(app_module, "BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()

    async def never_stops() -> None:
        await _asyncio.Event().wait()

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = MagicMock(spec=HeartbeatScheduler)
    heartbeat_scheduler.run = AsyncMock(side_effect=never_stops)
    heartbeat_scheduler.stop = MagicMock()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with TestClient(app):
        pass

    heartbeat_scheduler.stop.assert_called_once()
    relay.stop.assert_awaited_once()


def test_state_endpoint_returns_503_when_relay_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RELAY_ENABLED", "false")

    app = app_module.create_app()
    with TestClient(app) as client:
        response = client.get("/state")

    assert response.status_code == 503
    assert response.json() == {"detail": "relay is not enabled"}


def test_state_endpoint_returns_relay_state_when_enabled(
    monkeypatch: pytest.MonkeyPatch, relay_env: None
) -> None:
    monkeypatch.setenv("RELAY_ENABLED", "true")

    telegram_client = MagicMock(spec=TelegramClient)
    telegram_client.__aenter__ = AsyncMock(return_value=telegram_client)
    telegram_client.__aexit__ = AsyncMock(return_value=None)

    started_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    last_sms_received_at = datetime(2026, 5, 1, 12, 30, 45, tzinfo=UTC)
    relay_state = RelayState(
        status="running",
        started_at=started_at,
        last_sms_received_at=last_sms_received_at,
        last_error=None,
    )

    relay = MagicMock(spec=SmsRelay)
    relay.telegram_client = telegram_client
    relay.start = AsyncMock()
    relay.stop = AsyncMock()
    relay.state = MagicMock(return_value=relay_state)

    gauge_updater = _make_gauge_updater()
    watchdog = _make_watchdog()
    cleanup_scheduler = _make_cleanup_scheduler()
    heartbeat_scheduler = _make_heartbeat_scheduler()

    monkeypatch.setattr(
        app_module,
        "build_relay",
        MagicMock(
            return_value=(relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler)
        ),
    )

    app = app_module.create_app()
    with TestClient(app) as client:
        response = client.get("/state")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "running",
        "started_at": "2026-05-01T12:00:00Z",
        "last_sms_received_at": "2026-05-01T12:30:45Z",
        "last_error": None,
        "sms_delete_failures_count": 0,
        "last_delete_failure_at": None,
        "modem_state": None,
        "modem_signal_percent": None,
        "modem_operator": None,
        "modem_registration": None,
        "queue_pending_count": None,
        "queue_failed_count": None,
        "last_telegram_success_at": None,
    }
    assert isinstance(body["started_at"], str)
    assert isinstance(body["last_sms_received_at"], str)
