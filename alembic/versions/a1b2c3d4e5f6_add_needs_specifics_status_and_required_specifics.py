"""add needs_specifics status and required_specifics column

Revision ID: a1b2c3d4e5f6
Revises: dd06bb949617
Create Date: 2026-05-07 16:55:00.000000

Adds a `needs_specifics` value to the `item_status` enum and a
`required_specifics` JSONB column on `items` so the publisher can record
the eBay item-specific names a seller still owes us before the listing
can publish.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "dd06bb949617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres requires ALTER TYPE ... ADD VALUE outside a transaction block,
    # but alembic wraps each migration in one. COMMIT first, add the value,
    # then reopen the implicit transaction.
    op.execute("COMMIT")
    op.execute("ALTER TYPE item_status ADD VALUE IF NOT EXISTS 'needs_specifics'")

    op.add_column(
        "items",
        sa.Column(
            "required_specifics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("items", "required_specifics")
    # Removing an enum value in Postgres requires recreating the type. We
    # leave the value in place on downgrade — it's inert without code that
    # writes it. If a clean removal is needed, do it via a manual
    # data-migration script.
