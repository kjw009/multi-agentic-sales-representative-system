"""phase 5 autonomy + listing offer_id

Adds the per-seller autonomy & stale-reprice settings (autonomy_level,
stale_threshold_days, max_reprice_count) and the eBay offer ID on listings
(needed by the reprice flow — update_offer_price keys on offer_id, not
listing_id).

Revision ID: 0011_phase5
Revises: e12cea1746b1
Create Date: 2026-05-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_phase5"
down_revision: str | None = "e12cea1746b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUTONOMY_VALUES = ("draft", "auto_low_risk", "full_auto")


def upgrade() -> None:
    autonomy_enum = sa.Enum(*_AUTONOMY_VALUES, name="autonomy_level")
    autonomy_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "sellers",
        sa.Column(
            "autonomy_level",
            autonomy_enum,
            nullable=False,
            server_default=sa.text("'draft'::autonomy_level"),
        ),
    )
    op.add_column(
        "sellers",
        sa.Column(
            "stale_threshold_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("7"),
        ),
    )
    op.add_column(
        "sellers",
        sa.Column(
            "max_reprice_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )

    op.add_column(
        "listings",
        sa.Column("external_offer_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listings", "external_offer_id")
    op.drop_column("sellers", "max_reprice_count")
    op.drop_column("sellers", "stale_threshold_days")
    op.drop_column("sellers", "autonomy_level")
    sa.Enum(name="autonomy_level").drop(op.get_bind(), checkfirst=True)
