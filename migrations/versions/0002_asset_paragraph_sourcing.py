"""assets: ảnh theo từng đoạn lời đọc (script_version_id, paragraph_idx, search_query, origin_key)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 01:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('script_version_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('paragraph_idx', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('search_query', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('origin_key', sa.String(length=1000), nullable=True))
        batch_op.create_index(batch_op.f('ix_assets_script_version_id'), ['script_version_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assets_script_version_id'))
        batch_op.drop_column('origin_key')
        batch_op.drop_column('search_query')
        batch_op.drop_column('paragraph_idx')
        batch_op.drop_column('script_version_id')
