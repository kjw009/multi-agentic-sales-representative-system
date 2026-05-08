"""add ebay oauth state table

Revision ID: 4d68cffa046a
Revises: a1b2c3d4e5f6
Create Date: 2026-05-08 04:40:56.308107

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d68cffa046a"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ebay_oauth_states",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("seller_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["seller_id"], ["sellers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state"),
    )


def downgrade() -> None:
    op.drop_table("ebay_oauth_states")
