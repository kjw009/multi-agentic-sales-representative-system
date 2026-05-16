"""Add comparable similarity score

Revision ID: 0016_comparable_similarity
Revises: 0015_intake_visual_condition
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_comparable_similarity"
down_revision: str | None = "0015_intake_visual_condition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comparable_listings",
        sa.Column("similarity_score", sa.Numeric(5, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("comparable_listings", "similarity_score")
