"""Application settings. Every value is environment driven - nothing secret is hard-coded.

Every field that existed before is still here with the same name, so nothing that
imports ``settings`` needs to change. What is new is the crawl transport section:
robots policy, browser fallback, session warm-up and jitter, all of which were
previously either hard-coded in ``http.py`` or absent entirely.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, RedisDsn, field_validator
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
    DATABASE_URL: str = "postgresql://scrappy:scrappy@postgres:5432/scrappy"
    REDIS_URL: RedisDsn = "redis://redis:6379/0"  # type: ignore[assignment]

    # --- Crawl policy ------------------------------------------------------
    # Raised from 1.0/4. These four storefronts serve thousands of concurrent
    # shoppers; 2.5 requests per second from one client is well inside normal
    # traffic and is what makes a full-catalogue run finish in hours rather than
    # days. Tune per site in the `sites` table without a redeploy.
    DEFAULT_REQUESTS_PER_SECOND: float = 2.5
    DEFAULT_CONCURRENCY: int = 6
    REQUEST_TIMEOUT_SECONDS: float = 30.0
    MAX_RETRIES: int = 4
    MAX_PAGES_PER_SUBCATEGORY: int = 200
    # Random 0..N second wait added on top of the token bucket. Perfectly even
    # request spacing is itself a bot signal; a little jitter removes it.
    REQUEST_JITTER_SECONDS: float = 0.4

    # robots.txt handling.
    #   strict   - honour every rule (default).
    #   listings - honour robots.txt, but do not let a blanket `Disallow: /` or a
    #              `Disallow: /*?` query rule block a catalogue path an operator
    #              explicitly mapped. Many OpenCart robots.txt files ban query
    #              strings to protect faceted-search crawl budget, which also
    #              kills plain `?page=2` pagination.
    #   off      - do not consult robots.txt at all.
    # Anything other than `strict` is a decision about a specific site you have
    # a right to read. It is logged loudly on every job so it is never accidental.
    ROBOTS_POLICY: Literal["strict", "listings", "off"] = "strict"
    # Kept for backwards compatibility with existing .env files and callers.
    RESPECT_ROBOTS_TXT: bool = True

    # Transport.
    HTTP2_ENABLED: bool = True
    SESSION_WARMUP: bool = True
    # When true, send USER_AGENT instead of a rotating real-browser fingerprint.
    # Use this where you have written permission and want to be identifiable.
    USE_CUSTOM_USER_AGENT: bool = False
    USER_AGENT: str = (
        "ScrappyProBot/1.0 (+https://example.com/bot; contact=ops@example.com)"
    )

    # Headless-browser escalation, for origins that serve a JS challenge.
    BROWSER_FALLBACK_ENABLED: bool = True
    FORCE_BROWSER: bool = False  # skip HTTP entirely; per-site flag is preferred
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def effective_robots_policy(self) -> str:
        """ROBOTS_POLICY wins, but a legacy RESPECT_ROBOTS_TXT=false still disables."""
        if not self.RESPECT_ROBOTS_TXT and self.ROBOTS_POLICY == "strict":
            return "off"
        return self.ROBOTS_POLICY

    @property
    def database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)

    @property
    def sync_database_url(self) -> str:
        """Used by Alembic, which runs migrations synchronously."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()