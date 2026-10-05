"""folder / table AI context

Revision ID: a4c1e9d2b7f0
Revises: d7c67f32a7b3
Create Date: 2026-10-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4c1e9d2b7f0'
down_revision = 'd7c67f32a7b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('folder', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_context', sa.Text(), nullable=True))
    with op.batch_alter_table('inv_table', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_context', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('inv_table', schema=None) as batch_op:
        batch_op.drop_column('ai_context')
    with op.batch_alter_table('folder', schema=None) as batch_op:
        batch_op.drop_column('ai_context')
