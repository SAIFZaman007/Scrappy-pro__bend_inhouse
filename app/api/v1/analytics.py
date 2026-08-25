import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.entities import GlobalProduct, PriceHistory, ProductVariant, ScrapeJob
from app.schemas.models import (
    AnalyticsSummaryOut,
    GlobalProductListOut,
    GlobalProductOut,
    RunComparisonOut,
    RunComparisonRequest,
    SyncResultOut,
)
from app.services.matching import compare_two_runs, sync_all_historical_runs

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryOut)
async def get_analytics_summary(db: AsyncSession = Depends(get_db)) -> Any:
    """Retrieve market analytics overview metrics."""
    total_products = (await db.execute(select(func.count(GlobalProduct.id)))).scalar() or 0
    total_variants = (await db.execute(select(func.count(ProductVariant.id)))).scalar() or 0
    
    total_runs = (
        await db.execute(
            select(func.count(distinct(ScrapeJob.id))).where(ScrapeJob.status == "completed")
        )
    ).scalar() or 0

    # Count multi-store products (variants >= 2)
    multi_store_subq = (
        select(ProductVariant.global_id)
        .group_by(ProductVariant.global_id)
        .having(func.count(ProductVariant.id) >= 2)
        .subquery()
    )
    multi_store_matches = (
        await db.execute(select(func.count()).select_from(multi_store_subq))
    ).scalar() or 0

    # Max savings across variants
    savings_subq = (
        select(
            ProductVariant.global_id,
            (func.max(ProductVariant.latest_price) - func.min(ProductVariant.latest_price)).label("spread")
        )
        .where(ProductVariant.latest_price.isnot(None))
        .group_by(ProductVariant.global_id)
        .having(func.count(ProductVariant.id) >= 2)
        .subquery()
    )
    max_savings = (
        await db.execute(select(func.coalesce(func.max(savings_subq.c.spread), Decimal("0"))))
    ).scalar() or Decimal("0")

    return {
        "total_products": total_products,
        "multi_store_matches": multi_store_matches,
        "total_runs_indexed": total_runs,
        "max_savings": max_savings,
        "total_variants": total_variants,
    }


@router.post("/sync", response_model=SyncResultOut)
async def sync_catalog(db: AsyncSession = Depends(get_db)) -> Any:
    """Index and backfill all past completed scrape runs into the catalog."""
    return await sync_all_historical_runs(db)


@router.post("/compare-runs", response_model=RunComparisonOut)
async def compare_runs_endpoint(
    payload: RunComparisonRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Compare product matches, price deltas, and stock differences between two runs."""
    try:
        return await compare_two_runs(db, payload.run_a_id, payload.run_b_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/products", response_model=GlobalProductListOut)
async def list_global_products(
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(None, description="Search by product name or brand"),
    site_id: int | None = Query(None, description="Filter by site ID"),
    multi_store_only: bool = Query(False, description="Only show products available in 2+ retailers"),
    sort_by: str = Query("savings_desc", description="Sort order: savings_desc, name_asc, price_asc, price_desc, updated_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> Any:
    """List cross-retailer catalog products with multi-store comparison filters."""
    stmt = select(GlobalProduct)

    if q:
        search_terms = q.strip().split()
        for term in search_terms:
            stmt = stmt.where(
                (GlobalProduct.name.ilike(f"%{term}%")) | (GlobalProduct.brand.ilike(f"%{term}%"))
            )

    if site_id is not None:
        stmt = stmt.join(GlobalProduct.variants).where(ProductVariant.site_id == site_id)

    if multi_store_only:
        multi_subq = (
            select(ProductVariant.global_id)
            .group_by(ProductVariant.global_id)
            .having(func.count(ProductVariant.id) >= 2)
            .subquery()
        )
        stmt = stmt.where(GlobalProduct.id.in_(select(multi_subq.c.global_id)))

    # Get total count
    count_stmt = select(func.count(distinct(GlobalProduct.id))).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Sorting
    if sort_by == "name_asc":
        stmt = stmt.order_by(GlobalProduct.name.asc())
    elif sort_by == "updated_desc":
        stmt = stmt.order_by(GlobalProduct.created_at.desc())
    else:
        # Default order
        stmt = stmt.order_by(GlobalProduct.name.asc())

    # Get items with variants and history eager loaded
    stmt = (
        stmt.options(
            selectinload(GlobalProduct.variants).selectinload(ProductVariant.history)
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = (await db.execute(stmt)).scalars().all()

    # If sorted by savings_desc, sort in memory for the page
    if sort_by == "savings_desc":
        def get_savings(item: GlobalProduct) -> float:
            prices = [float(v.latest_price) for v in item.variants if v.latest_price is not None]
            if len(prices) >= 2:
                return max(prices) - min(prices)
            return 0.0
        items = sorted(items, key=get_savings, reverse=True)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/products/{product_id}", response_model=GlobalProductOut)
async def get_global_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """Retrieve full product details including retailer variants and historical price ledger."""
    stmt = (
        select(GlobalProduct)
        .where(GlobalProduct.id == product_id)
        .options(
            selectinload(GlobalProduct.variants)
            .selectinload(ProductVariant.history)
        )
    )
    product = (await db.execute(stmt)).scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return product

