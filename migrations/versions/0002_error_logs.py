"""error_logs — nhật ký lỗi của tool

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 07:40:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

INDEXES = ("fingerprint", "level", "source", "story_id", "last_seen_at", "resolved")


def upgrade() -> None:
    # DB tạo bằng create_all (init_db) có thể đã có bảng → bỏ qua để không lỗi
    if "error_logs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('error_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('fingerprint', sa.String(length=40), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('logger', sa.String(length=120), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('exc_type', sa.String(length=120), nullable=True),
    sa.Column('traceback', sa.Text(), nullable=True),
    sa.Column('story_id', sa.String(length=36), nullable=True),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('job_type', sa.String(length=40), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.String(length=100), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('error_logs', schema=None) as batch_op:
        for col in INDEXES:
            batch_op.create_index(batch_op.f(f'ix_error_logs_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('error_logs', schema=None) as batch_op:
        for col in INDEXES:
            batch_op.drop_index(batch_op.f(f'ix_error_logs_{col}'))
    op.drop_table('error_logs')
