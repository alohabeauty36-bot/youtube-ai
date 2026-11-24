"""essays — Phòng Bài luận (research web + viết bài luận theo chủ đề)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "essays" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('essays',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('topic', sa.Text(), nullable=False),
        sa.Column('requirements', sa.Text(), nullable=True),
        sa.Column('words', sa.Integer(), nullable=False),
        sa.Column('style', sa.String(length=20), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('stage_note', sa.String(length=200), nullable=True),
        sa.Column('dossier', sa.JSON(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('markdown', sa.Text(), nullable=True),
        sa.Column('qa', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('model', sa.String(length=80), nullable=True),
        sa.Column('usd', sa.Float(), nullable=False),
        sa.Column('memory_id', sa.Integer(), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_essays_created_at', 'essays', ['created_at'])
    op.create_index('ix_essays_status', 'essays', ['status'])


def downgrade() -> None:
    op.drop_table('essays')
