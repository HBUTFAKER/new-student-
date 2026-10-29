"""Application settings loaded from environment variables and .env."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
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
    database_url: str = "postgresql+asyncpg://app_user:app_user_dev_password@localhost:5432/student"
    # Alembic needs DDL and role-management privileges; runtime traffic must
    # never use this URL because a superuser/owner bypasses RLS.
    migration_database_url: str = (
        "postgresql+asyncpg://student:student_dev_password@localhost:5432/student"
    )
    db_echo: bool = False

    # Authentication. The default secret is development-only; production
    # startup fails unless JWT_SECRET_KEY is provided.
    jwt_secret_key: str = "dev-insecure-change-me"
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_seconds: int = 7200
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30

    # WeChat mini program. Stub mode keeps local/test/CI free of real appid
    # credentials; the client interface is replaceable in production.
    wechat_app_id: str = "wx_platform_stub"
    wechat_app_secret: str = ""
    wechat_stub_mode: bool = True

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def require_async_postgres_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value

    @model_validator(mode="after")
    def forbid_default_secret_in_production(self) -> "Settings":
        if self.app_env == "production" and self.jwt_secret_key == "dev-insecure-change-me":
            raise ValueError("JWT_SECRET_KEY must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""

    return Settings()
