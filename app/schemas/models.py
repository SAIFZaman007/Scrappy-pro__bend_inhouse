"""Request/response contracts. Pydantic validates every boundary of the API."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_admin: bool


# --- catalogue -------------------------------------------------------------
class SiteOut(ORMModel):
    id: int
    key: str
    name: str
    base_url: str
    is_enabled: bool
    requests_per_second: float
    concurrency: int
    notes: str | None = None
    mapped_subcategories: int = 0


class SubcategoryOut(ORMModel):
    id: int
    slug: str
    name: str
    is_mapped: bool = False
    is_verified: bool = False
    url_path: str | None = None


class CategoryOut(ORMModel):
    id: int
    slug: str
    name: str
    subcategories: list[SubcategoryOut] = []


class MappingUpdate(BaseModel):
    url_path: str = Field(min_length=1, max_length=500)
    is_verified: bool = False
    is_enabled: bool = True

    @field_validator("url_path")
    @classmethod
    def _must_be_relative_or_https(cls, v: str) -> str:
        v = v.strip()
        if v.startswith("http://"):
            raise ValueError("Use an https URL or a relative path")
        if not v.startswith(("/", "https://")):
            v = "/" + v
        return v


# --- jobs ------------------------------------------------------------------
class JobOptions(BaseModel):
    max_pages: int = Field(default=25, ge=1, le=200)
    # Kept so existing clients keep working. `detail_mode` supersedes it: when
    # both are sent, `detail_mode` wins.
    fetch_details: bool = True
    # all          - fetch every product page (richest, slowest)
    # missing_only - only fetch when the listing row lacks price/image/features.
    #                On StarTech that skips most products for the same columns,
    #                roughly a 5x reduction in requests on a full catalogue run.
    # off          - listing data only
    # Defaults to None, not "all", so the runner can tell an unset value from an
    # explicit one and still honour a legacy client that sent fetch_details=False.
    detail_mode: Literal["all", "missing_only", "off"] | None = None
    detail_concurrency: int = Field(default=4, ge=1, le=8)
    id_prefix: str = Field(default="NEW", min_length=1, max_length=10, pattern=r"^[A-Za-z0-9_]+$")


class JobCreate(BaseModel):
    site_id: int
    subcategory_ids: list[int] = Field(min_length=1, max_length=200)
    options: JobOptions = JobOptions()


class JobEvent(BaseModel):
    at: str
    level: str
    message: str


class JobOut(ORMModel):
    id: uuid.UUID
    site_id: int
    site_key: str | None = None
    site_name: str | None = None
    status: str
    subcategory_ids: list[int]
    options: dict
    total_units: int
    completed_units: int
    products_found: int
    pages_fetched: int
    progress_percent: int
    current_step: str | None
    error_message: str | None
    events: list[JobEvent] = []
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


# --- products / exports ----------------------------------------------------
class ProductOut(ORMModel):
    id: uuid.UUID
    sequence: int
    name: str
    brand: str | None
    category_name: str | None
    subcategory_name: str | None
    price: Decimal | None
    old_price: Decimal | None
    stock: str | None
    rating: float | None
    reviews: int | None
    badge: str | None
    image: str | None
    product_url: str


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    page: int
    page_size: int


class ExportOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    fmt: str
    filename: str
    row_count: int
    size_bytes: int
    created_at: datetime


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    queue: bool
    worker_alive: bool
    version: str


class PriceHistoryOut(ORMModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    price: Decimal | None
    stock: str | None
    timestamp: datetime


class ProductVariantOut(ORMModel):
    id: uuid.UUID
    global_id: uuid.UUID
    site_id: int
    external_id: str | None
    product_url: str
    latest_price: Decimal | None
    latest_stock: str | None
    updated_at: datetime
    history: list[PriceHistoryOut] = []


class GlobalProductOut(ORMModel):
    id: uuid.UUID
    name: str
    brand: str | None
    spec_hash: str
    created_at: datetime
    variants: list[ProductVariantOut] = []


class GlobalProductListOut(BaseModel):
    items: list[GlobalProductOut]
    total: int
    page: int
    page_size: int


class AnalyticsSummaryOut(BaseModel):
    total_products: int
    multi_store_matches: int
    total_runs_indexed: int
    max_savings: Decimal
    total_variants: int


class SyncResultOut(BaseModel):
    jobs_processed: int
    products_processed: int
    new_global_products: int
    variants_updated: int


class RunComparisonRequest(BaseModel):
    run_a_id: uuid.UUID
    run_b_id: uuid.UUID


class RunComparisonProduct(BaseModel):
    match_key: str
    name: str
    brand: str | None
    product_a_name: str | None = None
    product_b_name: str | None = None
    product_a_url: str | None = None
    product_b_url: str | None = None
    price_a: Decimal | None = None
    price_b: Decimal | None = None
    stock_a: str | None = None
    stock_b: str | None = None
    price_diff: Decimal | None = None  # price_b - price_a
    pct_diff: float | None = None
    cheaper_run: Literal["A", "B", "equal", "unknown"] = "unknown"


class RunComparisonSummary(BaseModel):
    total_in_a: int
    total_in_b: int
    matched_count: int
    only_in_a_count: int
    only_in_b_count: int
    cheaper_in_a_count: int
    cheaper_in_b_count: int
    equal_price_count: int
    max_price_diff: Decimal
    run_a_site_name: str
    run_b_site_name: str
    run_a_date: datetime
    run_b_date: datetime


class RunComparisonOut(BaseModel):
    summary: RunComparisonSummary
    matched: list[RunComparisonProduct]
    only_in_a: list[RunComparisonProduct]
    only_in_b: list[RunComparisonProduct]