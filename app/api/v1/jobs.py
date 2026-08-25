"""Job lifecycle: create, watch, cancel, inspect results, download exports."""

from __future__ import annotations

import uuid
import anyio
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import ExportFile, Product, ScrapeJob, Site, SiteCategoryMap, User
from app.schemas.models import (
    ExportOut,
    JobCreate,
    JobListOut,
    JobOut,
    ProductListOut,
    ProductOut,
)
from app.services.export import build_export

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])

ACTIVE_STATUSES = ("queued", "running")


def _queue(request: Request) -> ArqRedis:
    pool: ArqRedis | None = getattr(request.app.state, "arq", None)
    if pool is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The job queue is unavailable. Check that Redis is running.",
        )
    return pool


def _to_out(job: ScrapeJob, site: Site | None = None) -> JobOut:
    return JobOut.model_validate(job).model_copy(
        update={
            "progress_percent": job.progress_percent,
            "site_key": site.key if site else None,
            "site_name": site.name if site else None,
        }
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobOut:
    site = (
        await db.execute(select(Site).where(Site.id == payload.site_id, Site.is_enabled.is_(True)))
    ).scalar_one_or_none()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That site is not available.")

    active = (
        await db.execute(
            select(func.count(ScrapeJob.id)).where(
                ScrapeJob.user_id == user.id, ScrapeJob.status.in_(ACTIVE_STATUSES)
            )
        )
    ).scalar_one()
    if active >= settings.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You already have {active} runs in progress. Wait for one to finish.",
        )

    mapped = set(
        (
            await db.execute(
                select(SiteCategoryMap.subcategory_id).where(
                    SiteCategoryMap.site_id == site.id,
                    SiteCategoryMap.subcategory_id.in_(payload.subcategory_ids),
                    SiteCategoryMap.is_enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not mapped:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"None of those categories are mapped for {site.name} yet.",
        )

    job = ScrapeJob(
        user_id=user.id,
        site_id=site.id,
        subcategory_ids=sorted(mapped),
        options=payload.options.model_dump(),
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await _queue(request).enqueue_job("run_scrape_job", str(job.id), _job_id=f"scrape:{job.id}")
    return _to_out(job, site)


@router.get("", response_model=JobListOut)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobListOut:
    conditions = [ScrapeJob.user_id == user.id] if not user.is_admin else []
    if status_filter:
        conditions.append(ScrapeJob.status == status_filter)

    total = (
        await db.execute(select(func.count(ScrapeJob.id)).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ScrapeJob, Site)
            .join(Site, Site.id == ScrapeJob.site_id)
            .where(*conditions)
            .order_by(ScrapeJob.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return JobListOut(
        items=[_to_out(job, site) for job, site in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


async def _get_job(db: AsyncSession, job_id: uuid.UUID, user: User) -> tuple[ScrapeJob, Site]:
    row = (
        await db.execute(
            select(ScrapeJob, Site)
            .join(Site, Site.id == ScrapeJob.site_id)
            .where(ScrapeJob.id == job_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That run does not exist.")
    job, site = row
    if job.user_id != user.id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That run belongs to another account.")
    return job, site


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobOut:
    job, site = await _get_job(db, job_id, user)
    return _to_out(job, site)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobOut:
    job, site = await _get_job(db, job_id, user)
    if job.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This run already {job.status}; there is nothing to cancel."
        )
    # The runner checks this flag between categories and stops cleanly.
    job.status = "cancelled"
    await db.commit()
    await db.refresh(job)
    return _to_out(job, site)


@router.get("/{job_id}/products", response_model=ProductListOut)
async def list_products(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProductListOut:
    await _get_job(db, job_id, user)
    conditions = [Product.job_id == job_id]
    if search:
        conditions.append(Product.name.ilike(f"%{search}%"))

    total = (await db.execute(select(func.count(Product.id)).where(*conditions))).scalar_one()
    items = (
        (
            await db.execute(
                select(Product)
                .where(*conditions)
                .order_by(Product.sequence)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return ProductListOut(
        items=[ProductOut.model_validate(p) for p in items],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("/{job_id}/exports", response_model=ExportOut, status_code=status.HTTP_201_CREATED)
async def create_export(
    job_id: uuid.UUID,
    fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExportFile:
    job, _ = await _get_job(db, job_id, user)
    if job.products_found == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This run collected no products, so there is nothing to export."
        )
    return await build_export(db, job_id, fmt)


@router.get("/{job_id}/exports", response_model=list[ExportOut])
async def list_exports(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ExportFile]:
    await _get_job(db, job_id, user)
    return list(
        (
            await db.execute(
                select(ExportFile)
                .where(ExportFile.job_id == job_id)
                .order_by(ExportFile.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


@router.get("/{job_id}/exports/{export_id}/download")
async def download_export(
    job_id: uuid.UUID,
    export_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    await _get_job(db, job_id, user)
    record = (
        await db.execute(
            select(ExportFile).where(ExportFile.id == export_id, ExportFile.job_id == job_id)
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That file does not exist.")

    path = Path(record.path).resolve()
    # Defence in depth: never serve anything outside the export directory.
    export_root = Path(settings.EXPORT_DIR).resolve()
    if not str(path).startswith(str(export_root)) or not path.exists():
        raise HTTPException(
            status.HTTP_410_GONE, "That file has expired. Generate a new export."
        )

    media_type = (
        "text/csv"
        if record.fmt == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(path, media_type=media_type, filename=record.filename)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    job, _ = await _get_job(db, job_id, user)
    
    # Delete physical export files first
    exports = (
        await db.execute(select(ExportFile).where(ExportFile.job_id == job_id))
    ).scalars().all()
    
    for export in exports:
        path = anyio.Path(export.path)
        if await path.exists():
            await path.unlink()
            
    await db.delete(job)
    await db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_all_jobs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    # Get all jobs for the user
    jobs = (
        await db.execute(select(ScrapeJob).where(ScrapeJob.user_id == user.id))
    ).scalars().all()
    
    if not jobs:
        return
        
    job_ids = [job.id for job in jobs]
    
    # Delete physical export files for these jobs
    exports = (
        await db.execute(select(ExportFile).where(ExportFile.job_id.in_(job_ids)))
    ).scalars().all()
    
    for export in exports:
        path = anyio.Path(export.path)
        if await path.exists():
            await path.unlink()
            
    for job in jobs:
        await db.delete(job)
        
    await db.commit()
