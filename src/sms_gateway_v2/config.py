from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    host: str = "127.0.0.1"
    port: int = 8091
    log_level: str = "INFO"
    state_dir: Path = Path("./state")
    queue_sent_retention_days: int = Field(default=30, ge=0)
    queue_failed_retention_days: int = Field(default=30, ge=0)
    dedup_window_minutes: int = Field(default=1, ge=1)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_timeout_seconds: float = Field(default=10.0, ge=0.1)
    telegram_max_retries: int = Field(default=3, ge=1)
    worker_retry_schedule_seconds: Annotated[tuple[int, ...], NoDecode] = Field(
        default=(5, 30, 300, 1800, 7200, 21600, 86400)
    )
    metrics_path: str = "/metrics"
    relay_enabled: bool = False
    queue_gauge_interval_seconds: float = Field(default=30.0, ge=1.0)
    modem_watchdog_interval_seconds: float = Field(default=60.0, ge=10.0)
    modem_watchdog_signal_zero_threshold: int = Field(default=5, ge=1)
    modem_watchdog_bad_state_minutes: int = Field(default=10, ge=1)
    modem_sms_text_wait_timeout_seconds: float = Field(default=5.0, ge=0.0)
    cleanup_interval_seconds: float = Field(default=3600.0, ge=60.0)
    heartbeat_enabled: bool = True
    heartbeat_interval_seconds: float = Field(default=86400.0, ge=60.0)
    heartbeat_telegram_chat_id: str = ""

    @field_validator("worker_retry_schedule_seconds", mode="before")
    @classmethod
    def parse_worker_retry_schedule_seconds(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        except ValueError as exc:
            msg = "worker_retry_schedule_seconds must be a comma-separated list of integers"
            raise ValueError(msg) from exc

    @field_validator("worker_retry_schedule_seconds")
    @classmethod
    def validate_worker_retry_schedule_seconds(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) == 0:
            msg = "worker_retry_schedule_seconds must not be empty"
            raise ValueError(msg)
        if any(delay <= 0 for delay in value):
            msg = "worker_retry_schedule_seconds values must be positive integers"
            raise ValueError(msg)
        return value

    @field_validator("metrics_path")
    @classmethod
    def validate_metrics_path(cls, value: str) -> str:
        if not value.startswith("/"):
            msg = "metrics_path must start with '/'"
            raise ValueError(msg)
        return value

    @property
    def dedup_db_path(self) -> Path:
        return self.state_dir / "dedup.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
