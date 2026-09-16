"""ARQ worker. Runs scrape jobs off the request path so the API stays responsive.

Scale horizontally by running more worker containers; Redis hands each job to
exactly one worker. ``max_jobs`` bounds how many runs a single container takes on,
which matters because each run holds open HTTP connections to a retailer.

Heartbeat
---------
This worker writes an "I'm alive" key to Redis on startup and every ~10 seconds
after (see ``app.core.heartbeat``). ``/api/v1/health`` and the run page read that
key to tell a genuinely slow crawl apart from "no worker is listening".

Fail-fast startup
-----------------
The heartbeat only means something if the worker can actually do work. So
``on_startup`` proves the database is reachable *before* the first heartbeat is
written. A worker that cannot reach Postgres now crashes loudly (and Coolify
restarts it / shows it unhealthy) instead of heartbeating while every job
silently fails - which is what left runs stuck on "queued" in production.

Self-healing
------------
* ``run_scrape_job`` never lets a job die without recording why: any crash marks
  the row ``failed`` with a readable message.
* ``reconcile_stuck_jobs`` (every minute) compares rows still ``queued`` in
  Postgres against what ARQ actually knows. Lost enqueues are re-queued; jobs
  ARQ already finished/failed are marked ``failed``. No run can sit on
  "queued" forever again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from arq.jobs import Job, JobStatus
from sqlalchemy import select, text, update

from app.core.config import settings
from app.core.heartbeat import HEARTBEAT_KEY, HEARTBEAT_TTL_SECONDS
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.entities import ScrapeJob
from app.services.export import purge_expired_exports
from app.services.runner import run_job

configure_logging()
log = get_logger(__name__)

# A healthy worker claims a job within milliseconds; anything still "queued"
# after this long deserves a look from the reconciler.
STUCK_QUEUED_AFTER = timedelta(minutes=2)
ARQ_JOB_PREFIX = "scrape:"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def arq_job_id(job_id: str) -> str:
    return f"{ARQ_JOB_PREFIX}{job_id}"


async def _mark_failed(job_id: str, message: str) -> None:
    """Record a failure on a job that is still live. Uses its own session."""
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(ScrapeJob)
                .where(ScrapeJob.id == job_id, ScrapeJob.status.in_(("queued", "running")))
                .values(
                    status="failed",
                    error_message=message[:2000],
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - last line of defence, never raise from here
        log.exception("worker.mark_failed_error", job_id=job_id)


async def run_scrape_job(ctx: dict, job_id: str) -> None:
    log.info("worker.job_start", job_id=job_id)
    try:
        async with SessionLocal() as db:
            await run_job(db, job_id)

            # Phase 2: run matching engine to populate the global catalogue.
            from app.services.matching import match_job_products

            await match_job_products(db, job_id)
    except Exception as exc:
        log.exception("worker.job_crashed", job_id=job_id)
        await _mark_failed(
            job_id,
            f"The worker hit an unexpected error and stopped this run "
            f"({type(exc).__name__}). Check the worker logs, then run it again.",
        )
        raise
    log.info("worker.job_end", job_id=job_id)


async def reconcile_stuck_jobs(ctx: dict) -> None:
    """Heal the gap between Postgres ("queued") and Redis (what ARQ knows)."""
    redis = ctx["redis"]
    cutoff = datetime.now(UTC) - STUCK_QUEUED_AFTER
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(ScrapeJob.id).where(
                    ScrapeJob.status == "queued", ScrapeJob.created_at < cutoff
                )
            )
        ).scalars().all()

    for job_uuid in rows:
        job_id = str(job_uuid)
        arq_job = Job(arq_job_id(job_id), redis)
        state = await arq_job.status()

        if state in (JobStatus.queued, JobStatus.deferred, JobStatus.in_progress):
            continue  # ARQ has it; just busy.

        if state == JobStatus.complete:
            info = await arq_job.result_info()
            reason = "finished without starting"
            if info is not None and not info.success:
                reason = f"failed: {type(info.result).__name__}: {info.result}"
            log.warning("worker.reconcile_failed", job_id=job_id, reason=reason)
            await _mark_failed(
                job_id, f"This run never started - the worker {reason}"[:2000]
            )
            continue

        # not_found: the enqueue was lost (Redis flushed/restarted, API crashed
        # between commit and enqueue). Put it back on the queue.
        log.warning("worker.reconcile_requeue", job_id=job_id)
        await redis.enqueue_job("run_scrape_job", job_id, _job_id=arq_job_id(job_id))


async def cleanup_exports(ctx: dict) -> None:
    removed = purge_expired_exports()
    if removed:
        log.info("worker.exports_purged", removed=removed)


async def emit_heartbeat(ctx: dict) -> None:
    await ctx["redis"].set(HEARTBEAT_KEY, _now_iso(), ex=HEARTBEAT_TTL_SECONDS)


async def _assert_database_reachable() -> None:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        log.critical(
            "worker.database_unreachable",
            target=settings.database_host,
            error=f"{type(exc).__name__}: {exc}",
        )
        # Crash on purpose: no heartbeat, container restarts, problem is visible.
        raise RuntimeError(
            f"Worker cannot reach PostgreSQL at {settings.database_host}. "
            "Set DATABASE_URL (or POSTGRES_*) on the worker service."
        ) from exc


async def on_startup(ctx: dict) -> None:
    await _assert_database_reachable()
    await emit_heartbeat(ctx)
    log.info("worker.online", database=settings.database_host)


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
        cron(emit_heartbeat, second={0, 10, 20, 30, 40, 50}, run_at_startup=False),
        cron(reconcile_stuck_jobs, second={5}, unique=True),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(str(settings.REDIS_URL))
    max_jobs = 4
    job_timeout = 60 * 60 * 6  # a full-catalogue run can legitimately take hours
    keep_result = 60 * 60
    max_tries = 1  # a crawl is not idempotent; the reconciler handles recovery
    health_check_interval = 30