"""Add global product catalog

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24 18:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002_faster_default_crawl_policy'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. global_products
    op.create_table('global_products',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('brand', sa.String(length=120), nullable=True),
        sa.Column('spec_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_global_products_spec_hash'), 'global_products', ['spec_hash'], unique=True)
    
    # 2. product_variants
    op.create_table('product_variants',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('global_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=120), nullable=True),
        sa.Column('product_url', sa.String(length=1000), nullable=False),
        sa.Column('latest_price', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('latest_stock', sa.String(length=60), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['global_id'], ['global_products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('global_id', 'site_id', name='uq_variant_per_site')
    )
    op.create_index(op.f('ix_product_variants_global_id'), 'product_variants', ['global_id'], unique=False)
    
    # 3. price_history
    op.create_table('price_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('variant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('stock', sa.String(length=60), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_price_history_timestamp'), 'price_history', ['timestamp'], unique=False)
    op.create_index(op.f('ix_price_history_variant_id'), 'price_history', ['variant_id'], unique=False)


def downgrade() -> None:
    op.drop_table('price_history')
    op.drop_table('product_variants')
    op.drop_table('global_products')
