"""Database schema for Scrappy Pro.

Design notes
------------
* ``Category``/``Subcategory`` hold the *canonical* MAKgadgets taxonomy. It is the
  vocabulary the user selects from, and it never changes per site.
* ``SiteCategoryMap`` is the translation layer: it maps one canonical subcategory
  to the URL path that a specific retailer uses. This is the piece that keeps the
  four very different site structures behind a single UI.
* ``Product`` rows are append-only per job so historical runs stay reproducible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid

JobStatus = ENUM(
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="job_status",
    create_type=True,
)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    jobs: Mapped[list[ScrapeJob]] = relationship(back_populates="user")


class Site(Base, TimestampMixin):
    """A retailer we collect from, plus its crawl policy."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Politeness controls, tunable per site without a redeploy.
    requests_per_second: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    requires_browser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    mappings: Mapped[list[SiteCategoryMap]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class Category(Base):
    """Canonical top-level category from the MAKgadgets hierarchy."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    subcategories: Mapped[list[Subcategory]] = relationship(
        back_populates="category", cascade="all, delete-orphan", order_by="Subcategory.position"
    )


class Subcategory(Base):
    __tablename__ = "subcategories"
    __table_args__ = (UniqueConstraint("category_id", "slug", name="uq_subcategory_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    category: Mapped[Category] = relationship(back_populates="subcategories")
    mappings: Mapped[list[SiteCategoryMap]] = relationship(
        back_populates="subcategory", cascade="all, delete-orphan"
    )


class SiteCategoryMap(Base, TimestampMixin):
    """Canonical subcategory -> retailer specific listing path."""

    __tablename__ = "site_category_map"
    __table_args__ = (
        UniqueConstraint("site_id", "subcategory_id", name="uq_site_subcategory"),
        Index("ix_site_map_lookup", "site_id", "subcategory_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    subcategory_id: Mapped[int] = mapped_column(
        ForeignKey("subcategories.id", ondelete="CASCADE"), nullable=False
    )
    # Relative path appended to Site.base_url, e.g. "/component/processor".
    url_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Set to true once a human has confirmed the path returns the right products.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    site: Mapped[Site] = relationship(back_populates="mappings")
    subcategory: Mapped[Subcategory] = relationship(back_populates="mappings")


class ScrapeJob(Base, TimestampMixin):
    __tablename__ = "scrape_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(JobStatus, default="queued", nullable=False, index=True)

    subcategory_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Live progress, polled by the UI.
    total_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    products_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(255))
    events: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="jobs")
    site: Mapped[Site] = relationship()
    products: Mapped[list[Product]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    exports: Mapped[list[ExportFile]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    @property
    def progress_percent(self) -> int:
        if self.total_units <= 0:
            return 0
        return min(100, int(self.completed_units / self.total_units * 100))


class Product(Base):
    """One harvested product. Column names mirror the required export schema."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("job_id", "product_url", name="uq_product_per_job"),
        Index("ix_products_job_seq", "job_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    subcategory_id: Mapped[int | None] = mapped_column(ForeignKey("subcategories.id"))

    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120))
    product_url: Mapped[str] = mapped_column(String(1000), nullable=False)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    category_name: Mapped[str | None] = mapped_column(String(120))
    subcategory_name: Mapped[str | None] = mapped_column(String(120))

    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="BDT", nullable=False)
    stock: Mapped[str | None] = mapped_column(String(60))
    rating: Mapped[float | None] = mapped_column(Float)
    reviews: Mapped[int | None] = mapped_column(Integer)
    badge: Mapped[str | None] = mapped_column(String(120))

    image: Mapped[str | None] = mapped_column(Text)
    images: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    specs: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    job: Mapped[ScrapeJob] = relationship(back_populates="products")


class ExportFile(Base):
    __tablename__ = "export_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fmt: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    job: Mapped[ScrapeJob] = relationship(back_populates="exports")


class GlobalProduct(Base):
    """A deduplicated, persistent catalog item."""
    __tablename__ = "global_products"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="global_product", cascade="all, delete-orphan")


class ProductVariant(Base):
    """A specific retailer's listing for a GlobalProduct."""
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("global_id", "site_id", name="uq_variant_per_site"),
    )
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    global_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("global_products.id", ondelete="CASCADE"), nullable=False, index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120))
    product_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    
    latest_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    latest_stock: Mapped[str | None] = mapped_column(String(60))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    global_product: Mapped[GlobalProduct] = relationship(back_populates="variants")
    history: Mapped[list["PriceHistory"]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    site: Mapped[Site] = relationship()


class PriceHistory(Base):
    """Time-series ledger of price and stock changes."""
    __tablename__ = "price_history"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    stock: Mapped[str | None] = mapped_column(String(60))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    
    variant: Mapped[ProductVariant] = relationship(back_populates="history")
