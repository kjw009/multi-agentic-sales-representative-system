"""rls policies on seller-data tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that hold per-seller data and must be isolated.
# 'sellers' itself is excluded — it's protected by password auth, not RLS.
_TABLES = ["items", "item_images", "chat_messages", "platform_credentials"]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE means the policy applies even to the table owner (salesrep).
        # This ensures application bugs can't leak data even when running as the owner role.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY seller_isolation ON {table}
                USING (seller_id = current_setting('app.current_seller_id', TRUE)::uuid)
                WITH CHECK (seller_id = current_setting('app.current_seller_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS seller_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
