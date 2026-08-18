"""ARQ worker. Runs scrape jobs off the request path so the API stays responsive.

Scale horizontally by running more worker containers; Redis hands each job to
exactly one worker. ``max_jobs`` bounds how many runs a single container takes on,
which matters because each run holds open HTTP connections to a retailer.
"""
from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.services.export import purge_expired_exports
from app.services.runner import run_job

configure_logging()
log = get_logger(__name__)


async def run_scrape_job(ctx: dict, job_id: str) -> None:
    log.info("worker.job_start", job_id=job_id)
    async with SessionLocal() as db:
        await run_job(db, job_id)
    log.info("worker.job_end", job_id=job_id)


async def cleanup_exports(ctx: dict) -> None:
    removed = purge_expired_exports()
    if removed:
        log.info("worker.exports_purged", removed=removed)


class WorkerSettings:
    functions = [run_scrape_job]
    cron_jobs = [cron(cleanup_exports, hour={3}, minute=0)]
    redis_settings = RedisSettings.from_dsn(str(settings.REDIS_URL))
    max_jobs = 4
    job_timeout = 60 * 60 * 6  # a full-catalogue run can legitimately take hours
    keep_result = 60 * 60
    health_check_interval = 30
