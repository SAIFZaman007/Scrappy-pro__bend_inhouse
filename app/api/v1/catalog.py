"""Sites and the canonical category tree, annotated with per-site mapping state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.entities import Category, Site, SiteCategoryMap, Subcategory, User
from app.schemas.models import CategoryOut, MappingUpdate, SiteOut, SubcategoryOut

router = APIRouter(tags=["catalog"], dependencies=[Depends(get_current_user)])


@router.get("/sites", response_model=list[SiteOut])
async def list_sites(db: AsyncSession = Depends(get_db)) -> list[SiteOut]:
    counts = dict(
        (
            await db.execute(
                select(SiteCategoryMap.site_id, func.count(SiteCategoryMap.id))
                .where(SiteCategoryMap.is_enabled.is_(True))
                .group_by(SiteCategoryMap.site_id)
            )
        ).all()
    )
    sites = (
        (await db.execute(select(Site).where(Site.is_enabled.is_(True)).order_by(Site.id)))
        .scalars()
        .all()
    )
    return [
        SiteOut.model_validate(site).model_copy(
            update={"mapped_subcategories": counts.get(site.id, 0)}
        )
        for site in sites
    ]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    site_id: int | None = Query(default=None, description="Annotate availability for this site"),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryOut]:
    categories = (
        (
            await db.execute(
                select(Category)
                .options(selectinload(Category.subcategories))
                .order_by(Category.position)
            )
        )
        .scalars()
        .all()
    )

    mapping: dict[int, SiteCategoryMap] = {}
    if site_id is not None:
        rows = (
            (
                await db.execute(
                    select(SiteCategoryMap).where(
                        SiteCategoryMap.site_id == site_id,
                        SiteCategoryMap.is_enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        mapping = {row.subcategory_id: row for row in rows}

    result: list[CategoryOut] = []
    for category in categories:
        subs = []
        for sub in sorted(category.subcategories, key=lambda s: s.position):
            row = mapping.get(sub.id)
            subs.append(
                SubcategoryOut(
                    id=sub.id,
                    slug=sub.slug,
                    name=sub.name,
                    is_mapped=row is not None,
                    is_verified=bool(row and row.is_verified),
                    url_path=row.url_path if row else None,
                )
            )
        result.append(
            CategoryOut(id=category.id, slug=category.slug, name=category.name, subcategories=subs)
        )
    return result


@router.put(
    "/sites/{site_id}/mappings/{subcategory_id}",
    response_model=SubcategoryOut,
    dependencies=[Depends(require_admin)],
)
async def upsert_mapping(
    site_id: int,
    subcategory_id: int,
    payload: MappingUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> SubcategoryOut:
    subcategory = (
        await db.execute(select(Subcategory).where(Subcategory.id == subcategory_id))
    ).scalar_one_or_none()
    if subcategory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That subcategory does not exist.")

    row = (
        await db.execute(
            select(SiteCategoryMap).where(
                SiteCategoryMap.site_id == site_id,
                SiteCategoryMap.subcategory_id == subcategory_id,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = SiteCategoryMap(site_id=site_id, subcategory_id=subcategory_id)
        db.add(row)
    row.url_path = payload.url_path
    row.is_verified = payload.is_verified
    row.is_enabled = payload.is_enabled
    await db.commit()
    await db.refresh(row)

    return SubcategoryOut(
        id=subcategory.id,
        slug=subcategory.slug,
        name=subcategory.name,
        is_mapped=True,
        is_verified=row.is_verified,
        url_path=row.url_path,
    )
