"""Application settings loaded from environment variables and .env."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SubscribeTemplate(BaseModel):
    """One subscription-message template the mini program may ask for.

    ``template_id`` is the WeChat template id (or a stub in local/test/CI);
    ``type`` follows WeChat's one-time vs long-term dichotomy; ``scene`` is our
    own label (``lesson_remind`` / ``balance_warn`` / ...) so 6.2 can pick a
    template by business event instead of hard-coding ids in the client.
    """

    template_id: str = Field(min_length=1, max_length=64)
    type: Literal["one_time", "long_term"] = "one_time"
    scene: str = Field(default="generic", min_length=1, max_length=32)


def _default_subscribe_templates() -> list[SubscribeTemplate]:
    """Stub templates so local/test/CI never need a real WeChat account.

    Production must override ``WECHAT_SUBSCRIBE_TEMPLATES`` (JSON) with the ids
    taken from the mini-program console; the stub ids only exist to keep the
    whole chain testable offline.
    """

    return [
        SubscribeTemplate(template_id="stub_lesson_remind", type="one_time", scene="lesson_remind"),
        SubscribeTemplate(template_id="stub_balance_warn", type="one_time", scene="balance_warn"),
        SubscribeTemplate(
            template_id="stub_lesson_cancelled", type="long_term", scene="lesson_cancelled"
        ),
    ]


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

    # 5.4: subscription-message templates are backend config, never hard-coded in
    # the mini program. Env: WECHAT_SUBSCRIBE_TEMPLATES='[{"template_id": ...}]'.
    wechat_subscribe_templates: list[SubscribeTemplate] = Field(
        default_factory=_default_subscribe_templates
    )

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
