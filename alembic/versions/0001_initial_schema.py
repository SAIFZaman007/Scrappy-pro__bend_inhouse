"""Initial schema.

The first revision builds every table straight from the SQLAlchemy metadata. That
keeps this file short and guarantees it matches the models exactly. Every revision
after this one is a normal ``alembic revision --autogenerate`` diff.
"""
from __future__ import annotations

from alembic import op
from app.db.base import Base
from app.models import entities  # noqa: F401  (registers tables on the metadata)

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
