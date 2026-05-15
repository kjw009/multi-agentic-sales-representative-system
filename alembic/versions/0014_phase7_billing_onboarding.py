"""Phase 7 — billing + onboarding + demo columns on sellers

Adds:
  - sellers.onboarding_completed — lets the onboarding walkthrough be
    dismissed and not re-shown
  - sellers.is_demo — read-only demo account flag
  - sellers.stripe_customer_id, plan, subscription_status,
    stripe_subscription_id, current_period_end — Stripe billing

Revision ID: 0014_phase7a
Revises: 0013_phase6a
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_phase7a"
down_revision: str | None = "0013_phase6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE plan_tier AS ENUM ('free', 'pro')")
    op.execute(
        "CREATE TYPE subscription_status AS ENUM "
        "('none', 'trialing', 'active', 'past_due', 'canceled')"
    )

    # Onboarding + demo
    op.add_column("sellers", sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("sellers", sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="false"))

    # Stripe billing
    op.add_column("sellers", sa.Column("stripe_customer_id", sa.Text(), nullable=True))
    op.add_column(
        "sellers",
        sa.Column(
            "plan",
            sa.Enum("free", "pro", name="plan_tier", create_type=False),
            nullable=False,
            server_default="free",
        ),
    )
    op.add_column(
        "sellers",
        sa.Column(
            "subscription_status",
            sa.Enum("none", "trialing", "active", "past_due", "canceled", name="subscription_status", create_type=False),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column("sellers", sa.Column("stripe_subscription_id", sa.Text(), nullable=True))
    op.add_column("sellers", sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in (
        "current_period_end",
        "stripe_subscription_id",
        "subscription_status",
        "plan",
        "stripe_customer_id",
        "is_demo",
        "onboarding_completed",
    ):
        op.drop_column("sellers", col)
    op.execute("DROP TYPE IF EXISTS subscription_status")
    op.execute("DROP TYPE IF EXISTS plan_tier")
