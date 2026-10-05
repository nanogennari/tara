"""folder / table AI context can append instead of replace

Revision ID: b8e2f4a1c3d5
Revises: a4c1e9d2b7f0
Create Date: 2026-10-05 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e2f4a1c3d5'
down_revision = 'a4c1e9d2b7f0'
branch_labels = None
depends_on = None


def upgrade():
    # server_default: existing rows keep "replace" (SQLite can't add a NOT NULL column without one)
    with op.batch_alter_table('folder', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_context_append', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('inv_table', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_context_append', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('inv_table', schema=None) as batch_op:
        batch_op.drop_column('ai_context_append')
    with op.batch_alter_table('folder', schema=None) as batch_op:
        batch_op.drop_column('ai_context_append')
