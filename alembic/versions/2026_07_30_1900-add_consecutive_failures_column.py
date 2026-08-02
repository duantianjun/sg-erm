"""add consecutive_failures column to repository_source

Revision ID: b3f2a7c8d901
Revises: a98e106b6154
Create Date: 2026-07-30 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f2a7c8d901'
down_revision: Union[str, None] = 'a98e106b6154'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('repository_source', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('repository_source', schema=None) as batch_op:
        batch_op.drop_column('consecutive_failures')
