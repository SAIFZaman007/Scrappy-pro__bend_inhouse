"""Application settings. Every value is environment driven - nothing secret is hard-coded.

Every field that existed before is still here with the same name, so nothing that
imports ``settings`` needs to change. What is new is the crawl transport section:
robots policy, browser fallback, session warm-up and jitter, all of which were
previously either hard-coded in ``http.py`` or absent entirely.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, RedisDsn, field_validator, model_validator
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
    CORS_ORIGINS: str = "http://localhost:5173"
    FIRST_ADMIN_EMAIL: str = "admin@scrappy.local"
    FIRST_ADMIN_PASSWORD: str = Field(min_length=10)

    # --- Data stores -------------------------------------------------------
    DATABASE_URL: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None
    POSTGRES_HOST: str | None = None
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str | None = None
    REDIS_URL: RedisDsn = "redis://redis:6379/0"  # type: ignore[assignment]

    # --- Crawl policy ------------------------------------------------------
    DEFAULT_REQUESTS_PER_SECOND: float = 2.5
    DEFAULT_CONCURRENCY: int = 6
    REQUEST_TIMEOUT_SECONDS: float = 30.0
    MAX_RETRIES: int = 4
    MAX_PAGES_PER_SUBCATEGORY: int = 200
    REQUEST_JITTER_SECONDS: float = 0.4

    # robots.txt handling.
    ROBOTS_POLICY: Literal["strict", "listings", "off"] = "strict"
    RESPECT_ROBOTS_TXT: bool = True

    # Transport.
    HTTP2_ENABLED: bool = True
    SESSION_WARMUP: bool = True
    USE_CUSTOM_USER_AGENT: bool = False
    USER_AGENT: str = (
        "ScrappyProBot/1.0 (+https://example.com/bot; contact=ops@example.com)"
    )

    # Headless-browser escalation, for origins that serve a JS challenge.
    BROWSER_FALLBACK_ENABLED: bool = True
    FORCE_BROWSER: bool = False
    BROWSER_HEADLESS: bool = True
    BROWSER_TIMEOUT_SECONDS: float = 45.0

    # --- Job limits --------------------------------------------------------
    MAX_ACTIVE_JOBS_PER_USER: int = 3
    EXPORT_DIR: str = "/data/exports"
    EXPORT_RETENTION_HOURS: int = 72

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("ROBOTS_POLICY", mode="before")
    @classmethod
    def _normalise_policy(cls, v: object) -> object:
        """Let an existing RESPECT_ROBOTS_TXT=false keep working."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            self.DATABASE_URL = self.DATABASE_URL.strip()
            return self
        missing = [
            name
            for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_DB")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                "Database is not configured: set DATABASE_URL, or all of "
                f"POSTGRES_USER/PASSWORD/HOST/DB (missing: {', '.join(missing)})."
            )
        # Passwords with @ : / etc. must be percent-encoded inside a URL.
        self.DATABASE_URL = (
            f"postgresql://{quote(self.POSTGRES_USER, safe='')}:"
            f"{quote(self.POSTGRES_PASSWORD, safe='')}@{self.POSTGRES_HOST}:"
            f"{self.POSTGRES_PORT}/{quote(self.POSTGRES_DB, safe='')}"
        )
        return self

    @property
    def database_host(self) -> str:
        """Host:port/db only - safe to log (no credentials)."""
        return self.DATABASE_URL.rsplit("@", 1)[-1]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def effective_robots_policy(self) -> str:
        """ROBOTS_POLICY wins, but a legacy RESPECT_ROBOTS_TXT=false still disables."""
        if not self.RESPECT_ROBOTS_TXT and self.ROBOTS_POLICY == "strict":
            return "off"
        return self.ROBOTS_POLICY

    def _base_url(self) -> str:
        """Normalise any accepted scheme to plain ``postgresql://``."""
        url = str(self.DATABASE_URL)
        for prefix in ("postgres://", "postgresql+asyncpg://", "postgresql+psycopg://"):
            if url.startswith(prefix):
                return "postgresql://" + url[len(prefix):]
        return url

    @property
    def database_url(self) -> str:
        return self._base_url().replace("postgresql://", "postgresql+asyncpg://", 1)

    @property
    def sync_database_url(self) -> str:
        """Used by Alembic, which runs migrations synchronously."""
        return self._base_url().replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()