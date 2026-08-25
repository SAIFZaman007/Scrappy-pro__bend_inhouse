"""Raise default crawl pace now that the token-bucket burst-capacity bug is fixed.

Revision 0001 seeded every site with a burst capacity of effectively 1 request,
which meant the `concurrency` column never did anything - every request queued
behind the last one regardless of how many were allowed to run in parallel.
With that fixed (see app/scrapers/http.py), the old rate/concurrency pairs are
needlessly slow for these four storefronts. This updates existing rows only;
a fresh install picks the new numbers up directly from app/services/seed.py.

Still conservative. If a site starts pushing back (429s, or ChallengeDetected
entries in a run's tape), lower that one row rather than the whole fleet:

    UPDATE sites SET requests_per_second = 1.0, concurrency = 3 WHERE key = '<site>';
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_faster_default_crawl_policy"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

# key -> (requests_per_second, concurrency)
NEW_POLICY = {
    "startech": (3.0, 6),
    "techland": (2.5, 5),
    "ryans": (2.5, 5),
    "computermania": (2.0, 4),
}
OLD_POLICY = {
    "startech": (1.0, 4),
    "techland": (0.8, 3),
    "ryans": (0.8, 3),
    "computermania": (0.6, 2),
}


def _sites_table() -> sa.Table:
    return sa.table(
        "sites",
        sa.column("key", sa.String),
        sa.column("requests_per_second", sa.Float),
        sa.column("concurrency", sa.Integer),
    )


def upgrade() -> None:
    sites = _sites_table()
    conn = op.get_bind()
    for key, (rps, concurrency) in NEW_POLICY.items():
        conn.execute(
            sites.update()
            .where(sites.c.key == key)
            .values(requests_per_second=rps, concurrency=concurrency)
        )


def downgrade() -> None:
    sites = _sites_table()
    conn = op.get_bind()
    for key, (rps, concurrency) in OLD_POLICY.items():
        conn.execute(
            sites.update()
            .where(sites.c.key == key)
            .values(requests_per_second=rps, concurrency=concurrency)
        )