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

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8091
    assert settings.log_level == "INFO"
    assert settings.state_dir == Path("./state")
    assert settings.queue_sent_retention_days == 30
    assert settings.queue_failed_retention_days == 30
    assert settings.dedup_window_minutes == 1
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

    settings = get_settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.state_dir == tmp_path / "custom-state"
    assert settings.queue_sent_retention_days == 10
    assert settings.queue_failed_retention_days == 20
    assert settings.dedup_window_minutes == 5
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
