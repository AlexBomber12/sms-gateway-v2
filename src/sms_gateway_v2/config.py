from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    queue_sent_retention_days: int = 30
    queue_failed_retention_days: int = 30
    dedup_window_minutes: int = 1

    @property
    def dedup_db_path(self) -> Path:
        return self.state_dir / "dedup.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
