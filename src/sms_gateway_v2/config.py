from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    metrics_path: str = "/metrics"

    @property
    def dedup_db_path(self) -> Path:
        return self.state_dir / "dedup.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
