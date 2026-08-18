"""Application settings. Every value is environment driven - nothing secret is hard-coded."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Runtime -----------------------------------------------------------
    ENV: Literal["local", "staging", "production"] = "local"
    DEBUG: bool = False
    APP_NAME: str = "Scrappy Pro"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # --- Security ----------------------------------------------------------
    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_TTL_MINUTES: int = 60 * 12
    ALGORITHM: str = "HS256"
    # Comma separated list, e.g. "https://scrappy.example.com"
    CORS_ORIGINS: str = "http://localhost:5173"
    # Bootstrap account created on first boot.
    FIRST_ADMIN_EMAIL: str = "admin@scrappy.local"
    FIRST_ADMIN_PASSWORD: str = Field(min_length=10)

    # --- Data stores -------------------------------------------------------
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "scrappy"
    POSTGRES_PASSWORD: str = "scrappy"
    POSTGRES_DB: str = "scrappy"
    REDIS_URL: RedisDsn = "redis://redis:6379/0"  # type: ignore[assignment]

    # --- Crawl policy ------------------------------------------------------
    # These defaults are deliberately conservative. Raise them only if the
    # target site's terms of service and robots.txt allow it.
    RESPECT_ROBOTS_TXT: bool = True
    DEFAULT_REQUESTS_PER_SECOND: float = 1.0
    DEFAULT_CONCURRENCY: int = 4
    REQUEST_TIMEOUT_SECONDS: float = 25.0
    MAX_RETRIES: int = 3
    MAX_PAGES_PER_SUBCATEGORY: int = 200
    USER_AGENT: str = (
        "ScrappyProBot/1.0 (+https://example.com/bot; contact=ops@example.com)"
    )

    # --- Job limits --------------------------------------------------------
    MAX_ACTIVE_JOBS_PER_USER: int = 3
    EXPORT_DIR: str = "/data/exports"
    EXPORT_RETENTION_HOURS: int = 72

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """Used by Alembic, which runs migrations synchronously."""
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
