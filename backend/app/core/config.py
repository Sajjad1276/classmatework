from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ClassMate Work API"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://classmate:local-dev-only@localhost:5432/classmatework"
    )
    redis_url: str = "redis://localhost:6379/0"
    session_cookie_name: str = "classmatework_session"
    csrf_cookie_name: str = "classmatework_csrf"
    session_ttl_minutes: int = 480
    login_max_attempts: int = 8
    login_lockout_minutes: int = 15

    @property
    def secure_cookies(self) -> bool:
        return self.app_env in {"staging", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
