"""phase 5 reprice_events + buyer_messages.seller_edited

Adds the per-event reprice history table populated by
packages/agents/pricing/reprice.py, and the seller_edited flag on
buyer_messages used to surface the seller draft edit rate.

Revision ID: 0012_phase5b
Revises: 0011_phase5
Create Date: 2026-05-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_phase5b"
down_revision: str | None = "0011_phase5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reprice_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "seller_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("old_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_price", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "repriced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_reprice_events_seller_repriced_at",
        "reprice_events",
        ["seller_id", "repriced_at"],
    )

    op.add_column(
        "buyer_messages",
        sa.Column("seller_edited", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("buyer_messages", "seller_edited")
    op.drop_index("ix_reprice_events_seller_repriced_at", table_name="reprice_events")
    op.drop_table("reprice_events")
