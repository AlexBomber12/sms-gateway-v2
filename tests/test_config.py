from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sms_gateway_v2.config import Settings, get_settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("STATE_DIR", raising=False)
    monkeypatch.delenv("QUEUE_SENT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("QUEUE_FAILED_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("DEDUP_WINDOW_MINUTES", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_BASE", raising=False)
    monkeypatch.delenv("TELEGRAM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TELEGRAM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("WORKER_RETRY_SCHEDULE_SECONDS", raising=False)
    monkeypatch.delenv("METRICS_PATH", raising=False)
    monkeypatch.delenv("SMS_GATEWAY_GROUP_GID", raising=False)
    monkeypatch.delenv("MODEM_SMS_TEXT_WAIT_TIMEOUT_SECONDS", raising=False)

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8091
    assert settings.log_level == "INFO"
    assert settings.state_dir == Path("./state")
    assert settings.queue_sent_retention_days == 30
    assert settings.queue_failed_retention_days == 30
    assert settings.dedup_window_minutes == 1
    assert settings.telegram_bot_token == ""
    assert settings.telegram_chat_id == ""
    assert settings.telegram_api_base == "https://api.telegram.org"
    assert settings.telegram_timeout_seconds == 10.0
    assert settings.telegram_max_retries == 3
    assert settings.worker_retry_schedule_seconds == (5, 30, 300, 1800, 7200, 21600, 86400)
    assert settings.metrics_path == "/metrics"
    assert settings.sms_gateway_group_gid == ""
    assert settings.modem_sms_text_wait_timeout_seconds == 5.0
    assert settings.dedup_db_path == Path("./state/dedup.db")


def test_settings_env_overrides_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "custom-state"))
    monkeypatch.setenv("QUEUE_SENT_RETENTION_DAYS", "10")
    monkeypatch.setenv("QUEUE_FAILED_RETENTION_DAYS", "20")
    monkeypatch.setenv("DEDUP_WINDOW_MINUTES", "5")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("TELEGRAM_API_BASE", "https://telegram.example")
    monkeypatch.setenv("TELEGRAM_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("TELEGRAM_MAX_RETRIES", "5")
    monkeypatch.setenv("WORKER_RETRY_SCHEDULE_SECONDS", "1,2,4")
    monkeypatch.setenv("METRICS_PATH", "/custom-metrics")
    monkeypatch.setenv("SMS_GATEWAY_GROUP_GID", "995")
    monkeypatch.setenv("MODEM_SMS_TEXT_WAIT_TIMEOUT_SECONDS", "0.5")

    settings = get_settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.state_dir == tmp_path / "custom-state"
    assert settings.queue_sent_retention_days == 10
    assert settings.queue_failed_retention_days == 20
    assert settings.dedup_window_minutes == 5
    assert settings.telegram_bot_token == "token"
    assert settings.telegram_chat_id == "-100"
    assert settings.telegram_api_base == "https://telegram.example"
    assert settings.telegram_timeout_seconds == 2.5
    assert settings.telegram_max_retries == 5
    assert settings.worker_retry_schedule_seconds == (1, 2, 4)
    assert settings.metrics_path == "/custom-metrics"
    assert settings.sms_gateway_group_gid == "995"
    assert settings.modem_sms_text_wait_timeout_seconds == 0.5
    assert settings.dedup_db_path == tmp_path / "custom-state" / "dedup.db"


@pytest.mark.parametrize(
    ("env_name", "field_name"),
    [
        ("QUEUE_SENT_RETENTION_DAYS", "queue_sent_retention_days"),
        ("QUEUE_FAILED_RETENTION_DAYS", "queue_failed_retention_days"),
    ],
)
def test_settings_rejects_negative_queue_retention_days(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_name: str,
    field_name: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUEUE_SENT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("QUEUE_FAILED_RETENTION_DAYS", raising=False)
    monkeypatch.setenv(env_name, "-1")

    with pytest.raises(ValidationError, match=field_name):
        Settings()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_settings_rejects_non_positive_dedup_window_minutes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEDUP_WINDOW_MINUTES", value)

    with pytest.raises(ValidationError, match="dedup_window_minutes"):
        Settings()


@pytest.mark.parametrize("value", ["0", "0.09"])
def test_settings_rejects_too_low_telegram_timeout_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError, match="telegram_timeout_seconds"):
        Settings()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_settings_rejects_non_positive_telegram_max_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_MAX_RETRIES", value)

    with pytest.raises(ValidationError, match="telegram_max_retries"):
        Settings()


@pytest.mark.parametrize("value", ["", "0", "-1", "1,0", "1,-1"])
def test_settings_rejects_invalid_worker_retry_schedule_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKER_RETRY_SCHEDULE_SECONDS", value)

    with pytest.raises(ValidationError, match="worker_retry_schedule_seconds"):
        Settings()


def test_settings_rejects_non_integer_worker_retry_schedule_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKER_RETRY_SCHEDULE_SECONDS", "1,nope")

    with pytest.raises(ValidationError, match="worker_retry_schedule_seconds"):
        Settings()


def test_settings_rejects_metrics_path_without_leading_slash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("METRICS_PATH", "metrics")

    with pytest.raises(ValidationError, match="metrics_path"):
        Settings()
