"""ARQ worker. Runs scrape jobs off the request path so the API stays responsive.

Scale horizontally by running more worker containers; Redis hands each job to
exactly one worker. ``max_jobs`` bounds how many runs a single container takes on,
which matters because each run holds open HTTP connections to a retailer.

Heartbeat
---------
This worker writes an "I'm alive" key to Redis on startup and every ~10 seconds
after (see ``app.core.heartbeat``). ``/api/v1/health`` and the run page read that
key to tell a genuinely slow crawl apart from the single most common local-dev
mistake: starting the API but never starting this worker, so a job sits on
"queued" forever with nothing in the UI explaining why.
"""

from __future__ import annotations

from datetime import UTC, datetime

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.heartbeat import HEARTBEAT_KEY, HEARTBEAT_TTL_SECONDS
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.services.export import purge_expired_exports
from app.services.runner import run_job

configure_logging()
log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def run_scrape_job(ctx: dict, job_id: str) -> None:
    log.info("worker.job_start", job_id=job_id)
    async with SessionLocal() as db:
        await run_job(db, job_id)
        
        # Phase 2: Run matching engine to populate global catalog
        from app.services.matching import match_job_products
        await match_job_products(db, job_id)
    log.info("worker.job_end", job_id=job_id)


async def cleanup_exports(ctx: dict) -> None:
    removed = purge_expired_exports()
    if removed:
        log.info("worker.exports_purged", removed=removed)


async def emit_heartbeat(ctx: dict) -> None:
    await ctx["redis"].set(HEARTBEAT_KEY, _now_iso(), ex=HEARTBEAT_TTL_SECONDS)


async def on_startup(ctx: dict) -> None:
    await emit_heartbeat(ctx)
    log.info("worker.online")


async def on_shutdown(ctx: dict) -> None:
    try:
        await ctx["redis"].delete(HEARTBEAT_KEY)
    finally:
        log.info("worker.offline")


class WorkerSettings:
    functions = [run_scrape_job]
    cron_jobs = [
        cron(cleanup_exports, hour={3}, minute=0),
        # A fresh heartbeat every ~10s. TTL (25s) is longer than the interval
        # so one slow tick doesn't flip the UI to "offline" by mistake, but an
        # unclean crash (no on_shutdown) is still detected within ~25s.
        cron(emit_heartbeat, second={0, 10, 20, 30, 40, 50}),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(str(settings.REDIS_URL))
    max_jobs = 4
    job_timeout = 60 * 60 * 6  # a full-catalogue run can legitimately take hours
    keep_result = 60 * 60
    health_check_interval = 30