"""Application settings loaded from environment variables and .env."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Only non-secret defaults are stored here. Real credentials must come from
    environment variables or an untracked .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Student Management API"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://student:student@localhost:5432/student"
    db_echo: bool = False

    @field_validator("database_url")
    @classmethod
    def require_async_postgres_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""

    return Settings()
