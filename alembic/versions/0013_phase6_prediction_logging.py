"""Phase 6.0 — price prediction logging tables

Adds the three tables needed for the ML retraining loop data-capture:
  - model_versions: artifact registry
  - price_predictions: point-in-time feature snapshot per Agent 2 call
  - comparable_listings: validated eBay comparables per prediction

Seeds the current v3 model as the active version so Agent 2 can tag
predictions with a model_version_id from day one.

Revision ID: 0013_phase6a
Revises: 0012_phase5b
Create Date: 2026-05-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

from alembic import op

revision: str = "0013_phase6a"
down_revision: str | None = "0012_phase5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE IF NOT EXISTS model_status AS ENUM ('training', 'shadow', 'active', 'archived', 'failed')"
    )

    op.create_table(
        "model_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.Text(), nullable=False, server_default="lightgbm"),
        sa.Column("artifact_s3_key", sa.Text(), nullable=True),
        sa.Column("feature_cols", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("train_metrics", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("shadow_metrics", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("training_row_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            ENUM(
                "training",
                "shadow",
                "active",
                "archived",
                "failed",
                name="model_status",
                create_type=False,
            ),
            nullable=False,
            server_default="training",
        ),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # At most one active model at a time
    op.create_index(
        "ix_model_versions_active_unique",
        "model_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "price_predictions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "seller_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "item_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "listing_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "model_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_versions.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("features", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("features_partial", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("model_prediction", sa.Numeric(12, 2), nullable=True),
        sa.Column("comparable_median", sa.Numeric(12, 2), nullable=True),
        sa.Column("recommended_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("min_acceptable_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_shadow", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("realized_sale_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("realized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_price_predictions_model_shadow",
        "price_predictions",
        ["model_version_id", "is_shadow"],
    )

    op.create_table(
        "comparable_listings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "price_prediction_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_predictions.id", ondelete="CASCADE"),
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
        sa.Column("external_item_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("listing_url", sa.Text(), nullable=True),
        sa.Column("relevance", sa.Text(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # RLS policies mirroring 0002_rls_policies.py
    for table in ("price_predictions", "comparable_listings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_seller_isolation ON {table}
            USING (seller_id = current_setting('app.current_seller_id', true)::uuid)
        """)

    # Seed the current v3 model as active
    v3_id = str(uuid.uuid4())
    op.execute(f"""
        INSERT INTO model_versions (id, version, algorithm, status, notes, trained_at, promoted_at)
        VALUES (
            '{v3_id}',
            'v3',
            'lightgbm',
            'active',
            'Initial production model — LightGBM v3, trained on eBay UK active listings',
            NOW(),
            NOW()
        )
    """)


def downgrade() -> None:
    for table in ("price_predictions", "comparable_listings"):
        op.execute(f"DROP POLICY IF EXISTS {table}_seller_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_price_predictions_model_shadow", table_name="price_predictions")
    op.drop_index("ix_model_versions_active_unique", table_name="model_versions")
    op.drop_table("comparable_listings")
    op.drop_table("price_predictions")
    op.drop_table("model_versions")
    op.execute("DROP TYPE IF EXISTS model_status")
