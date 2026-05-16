"""add negotiating_posture to items

Revision ID: 8428a72411ee
Revises: a1b2c3d4e5f6
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8428a72411ee"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("negotiating_posture", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "negotiating_posture")
