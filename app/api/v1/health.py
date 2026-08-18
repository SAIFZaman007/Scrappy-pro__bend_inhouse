"""Liveness + dependency probe. Coolify points its health check here.

``worker_alive`` reflects the ARQ worker's heartbeat (app.core.heartbeat), not
just whether Redis is reachable. Redis can be perfectly healthy with zero
workers listening - that's exactly the state that leaves jobs stuck on
"queued" with nothing to explain why.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.heartbeat import HEARTBEAT_KEY
from app.db.session import get_db
from app.schemas.models import HealthOut

router = APIRouter(tags=["system"])
VERSION = "1.0.0"


@router.get("/health", response_model=HealthOut)
async def health(request: Request, db: AsyncSession = Depends(get_db)) -> HealthOut:
    database = queue = worker_alive = False
    try:
        await db.execute(text("SELECT 1"))
        database = True
    except Exception:  # noqa: BLE001
        database = False

    pool = getattr(request.app.state, "arq", None)
    if pool is not None:
        try:
            await pool.ping()
            queue = True
        except Exception:  # noqa: BLE001
            queue = False
        try:
            worker_alive = await pool.get(HEARTBEAT_KEY) is not None
        except Exception:  # noqa: BLE001
            worker_alive = False

    return HealthOut(
        status="ok" if database and queue and worker_alive else "degraded",
        database=database,
        queue=queue,
        worker_alive=worker_alive,
        version=VERSION,
    )