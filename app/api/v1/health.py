from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.models import HealthOut

router = APIRouter(tags=["system"])
VERSION = "1.0.0"


@router.get("/health", response_model=HealthOut)
async def health(request: Request, db: AsyncSession = Depends(get_db)) -> HealthOut:
    """Liveness + dependency probe. Coolify points its health check here."""
    database = queue = False
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
    return HealthOut(
        status="ok" if database and queue else "degraded",
        database=database,
        queue=queue,
        version=VERSION,
    )
