from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://classmate:local-dev-only@localhost:5432/classmatework"
    )
    # Separate connection to the live ClassMate product database. It must never
    # inherit ClassMate Work's schema search_path.
    c2_db_url: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: SecretStr | None = None
    bot_fsm_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    bot_update_lease_seconds: int = Field(default=180, ge=30, le=3600)
    bot_message_rate_limit: int = Field(default=30, ge=1, le=1000)
    bot_callback_rate_limit: int = Field(default=45, ge=1, le=1000)
    bot_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    classmate_telemetry_hash_key: SecretStr | None = None

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            aliases = {
                "dev": "development",
                "stage": "staging",
                "prod": "production",
            }
            normalized = value.strip().lower()
            return aliases.get(normalized, normalized)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
