"""budget_ledger, agent_memories, agent_skills — Ban Tài chính + bộ não phòng ban

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def _has(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has("budget_ledger"):
        op.create_table('budget_ledger',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('department', sa.String(length=30), nullable=False),
            sa.Column('provider', sa.String(length=20), nullable=False),
            sa.Column('model', sa.String(length=80), nullable=False),
            sa.Column('story_id', sa.String(length=36), nullable=True),
            sa.Column('status', sa.String(length=10), nullable=False),
            sa.Column('input_tokens', sa.Integer(), nullable=False),
            sa.Column('output_tokens', sa.Integer(), nullable=False),
            sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
            sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
            sa.Column('usd', sa.Float(), nullable=False),
            sa.Column('note', sa.String(length=200), nullable=True),
            sa.PrimaryKeyConstraint('id'))
        for col in ("at", "department", "story_id", "status"):
            op.create_index(f'ix_budget_ledger_{col}', 'budget_ledger', [col])
    if not _has("agent_memories"):
        op.create_table('agent_memories',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('department', sa.String(length=30), nullable=False),
            sa.Column('kind', sa.String(length=20), nullable=False),
            sa.Column('key_hash', sa.String(length=64), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False),
            sa.Column('model', sa.String(length=80), nullable=True),
            sa.Column('request', sa.Text(), nullable=True),
            sa.Column('response', sa.JSON(), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('quality', sa.Float(), nullable=False),
            sa.Column('approved', sa.Boolean(), nullable=False),
            sa.Column('uses', sa.Integer(), nullable=False),
            sa.Column('story_id', sa.String(length=36), nullable=True),
            sa.PrimaryKeyConstraint('id'))
        for col in ("created_at", "department", "kind", "key_hash", "story_id"):
            op.create_index(f'ix_agent_memories_{col}', 'agent_memories', [col])
    if not _has("agent_skills"):
        op.create_table('agent_skills',
            sa.Column('department', sa.String(length=30), nullable=False),
            sa.Column('mode', sa.String(length=20), nullable=False),
            sa.Column('teacher_calls', sa.Integer(), nullable=False),
            sa.Column('student_attempts', sa.Integer(), nullable=False),
            sa.Column('student_passes', sa.Integer(), nullable=False),
            sa.Column('student_live', sa.Integer(), nullable=False),
            sa.Column('cache_hits', sa.Integer(), nullable=False),
            sa.Column('recent', sa.JSON(), nullable=False),
            sa.Column('similarity_avg', sa.Float(), nullable=False),
            sa.Column('graduated', sa.Boolean(), nullable=False),
            sa.Column('saved_usd', sa.Float(), nullable=False),
            sa.Column('last_reflect_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('department'))


def downgrade() -> None:
    op.drop_table('agent_skills')
    op.drop_table('agent_memories')
    op.drop_table('budget_ledger')
