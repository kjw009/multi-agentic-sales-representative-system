"""add sellers, items, item_images, chat_messages, platform_credentials

Revision ID: 0001
Revises:
Create Date: 2026-04-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE item_condition AS ENUM ('new', 'like_new', 'good', 'fair', 'poor');
        EXCEPTION WHEN duplicate_object THEN null; END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE item_status AS ENUM (
                'pending', 'intake_in_progress', 'intake_complete',
                'priced', 'publishing', 'live', 'sold', 'removed', 'error'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE chat_role AS ENUM ('user', 'assistant');
        EXCEPTION WHEN duplicate_object THEN null; END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE platform AS ENUM ('ebay');
        EXCEPTION WHEN duplicate_object THEN null; END $$
        """
    )

    op.create_table(
        "sellers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_sellers_email", "sellers", ["email"], unique=True)

    op.create_table(
        "items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(255)),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("subcategory", sa.String(100)),
        sa.Column("condition", sa.Enum("new", "like_new", "good", "fair", "poor", name="item_condition", create_type=False), nullable=False),
        sa.Column("age_months", sa.SmallInteger()),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seller_floor_price", sa.Numeric(12, 2)),
        sa.Column("status", sa.Enum("pending", "intake_in_progress", "intake_complete", "priced", "publishing", "live", "sold", "removed", "error", name="item_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_items_seller_id", "items", ["seller_id"])

    op.create_table(
        "item_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("s3_key", sa.String(1024), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_item_images_item_id", "item_images", ["item_id"])
    op.create_index("ix_item_images_seller_id", "item_images", ["seller_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("role", sa.Enum("user", "assistant", name="chat_role", create_type=False), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_chat_messages_seller_id", "chat_messages", ["seller_id"])
    op.create_index("ix_chat_messages_item_id", "chat_messages", ["item_id"])

    op.create_table(
        "platform_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.Enum("ebay", name="platform", create_type=False), nullable=False),
        sa.Column("oauth_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("seller_id", "platform", name="uq_platform_credentials_seller_platform"),
    )
    op.create_index("ix_platform_credentials_seller_id", "platform_credentials", ["seller_id"])


def downgrade() -> None:
    op.drop_table("platform_credentials")
    op.drop_table("chat_messages")
    op.drop_table("item_images")
    op.drop_table("items")
    op.drop_table("sellers")

    op.execute("DROP TYPE IF EXISTS platform")
    op.execute("DROP TYPE IF EXISTS chat_role")
    op.execute("DROP TYPE IF EXISTS item_status")
    op.execute("DROP TYPE IF EXISTS item_condition")
