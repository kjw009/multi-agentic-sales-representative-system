"""add_listings_table

Revision ID: cbc1e4949ecc
Revises: 4705c798e249
Create Date: 2026-05-02 18:22:27.125564

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cbc1e4949ecc"
down_revision: str | None = "4705c798e249"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create the listing_status enum type (platform enum already exists)
    op.execute("CREATE TYPE listing_status AS ENUM ('publishing', 'live', 'ended', 'error')")

    op.create_table(
        "listings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("seller_id", sa.UUID(), nullable=False),
        sa.Column(
            "platform", postgresql.ENUM("ebay", name="platform", create_type=False), nullable=False
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "publishing", "live", "ended", "error", name="listing_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("posted_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        sa.Column("reprice_count", sa.Integer(), nullable=False),
        sa.Column("last_repriced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_buyer_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_id"], ["sellers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "platform", name="uq_listings_item_platform"),
    )
    op.create_index(op.f("ix_listings_item_id"), "listings", ["item_id"], unique=False)
    op.create_index(op.f("ix_listings_seller_id"), "listings", ["seller_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_listings_seller_id"), table_name="listings")
    op.drop_index(op.f("ix_listings_item_id"), table_name="listings")
    op.drop_table("listings")
    op.execute("DROP TYPE IF EXISTS listing_status")
