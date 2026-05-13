"""add sns_topic_arn to sellers

Revision ID: 3c337d1046f0
Revises: f72fb79a80a2
Create Date: 2026-05-14 00:32:56.815070

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c337d1046f0'
down_revision: Union[str, None] = 'f72fb79a80a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sellers", sa.Column("sns_topic_arn", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("sellers", "sns_topic_arn")
