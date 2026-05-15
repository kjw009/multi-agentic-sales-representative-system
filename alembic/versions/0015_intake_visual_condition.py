"""Add intake visual condition analysis fields

Revision ID: 0015_intake_visual_condition
Revises: 0014_phase7a
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_intake_visual_condition"
down_revision: str | None = "0014_phase7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("visual_condition_report", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "items",
        sa.Column("visual_condition_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "visual_condition_analyzed_at")
    op.drop_column("items", "visual_condition_report")
