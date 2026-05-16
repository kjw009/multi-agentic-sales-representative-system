"""listing approval gate

Adds a default-on seller setting that pauses newly priced listings for
seller approval, plus a listing status that the chat UI can poll.

Revision ID: 0017_listing_approval
Revises: 0016_comparable_similarity
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_listing_approval"
down_revision: str | None = "0016_comparable_similarity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'pending_approval' BEFORE 'publishing'"
    )
    op.add_column(
        "sellers",
        sa.Column(
            "require_listing_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sellers", "require_listing_approval")
    # PostgreSQL cannot drop enum values directly. Rebuild the enum only if
    # no rows still use the approval status.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM listings WHERE status = 'pending_approval') THEN
                RAISE EXCEPTION 'Cannot downgrade while listings are pending approval';
            END IF;
        END $$;
    """)
    op.execute("ALTER TYPE listing_status RENAME TO listing_status_old")
    op.execute("CREATE TYPE listing_status AS ENUM ('publishing', 'live', 'ended', 'error')")
    op.execute("ALTER TABLE listings ALTER COLUMN status DROP DEFAULT")
    op.execute("""
        ALTER TABLE listings
        ALTER COLUMN status TYPE listing_status
        USING status::text::listing_status
    """)
    op.execute("ALTER TABLE listings ALTER COLUMN status SET DEFAULT 'publishing'::listing_status")
    op.execute("DROP TYPE listing_status_old")
