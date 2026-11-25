"""growth — Ban Tăng trưởng: số liệu YouTube Analytics theo ngày/video + kế hoạch theo KPI doanh thu

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25 08:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "channel_metric_days" not in existing:
        op.create_table('channel_metric_days',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('channel_id', sa.String(length=64), nullable=False),
            sa.Column('day', sa.String(length=10), nullable=False),
            sa.Column('views', sa.Integer(), nullable=False),
            sa.Column('watch_minutes', sa.Float(), nullable=False),
            sa.Column('avg_view_seconds', sa.Float(), nullable=False),
            sa.Column('subs_gained', sa.Integer(), nullable=False),
            sa.Column('subs_lost', sa.Integer(), nullable=False),
            sa.Column('revenue_usd', sa.Float(), nullable=True),
            sa.Column('ad_revenue_usd', sa.Float(), nullable=True),
            sa.Column('playback_cpm_usd', sa.Float(), nullable=True),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('is_demo', sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('channel_id', 'day', name='uq_metric_day'))
        op.create_index('ix_channel_metric_days_channel_id', 'channel_metric_days', ['channel_id'])
        op.create_index('ix_channel_metric_days_day', 'channel_metric_days', ['day'])
    if "video_metrics" not in existing:
        op.create_table('video_metrics',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('channel_id', sa.String(length=64), nullable=False),
            sa.Column('youtube_video_id', sa.String(length=64), nullable=False),
            sa.Column('title', sa.Text(), nullable=True),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('privacy', sa.String(length=20), nullable=True),
            sa.Column('story_id', sa.String(length=36), nullable=True),
            sa.Column('pillar', sa.String(length=4), nullable=True),
            sa.Column('period_start', sa.String(length=10), nullable=False),
            sa.Column('period_end', sa.String(length=10), nullable=False),
            sa.Column('views', sa.Integer(), nullable=False),
            sa.Column('watch_minutes', sa.Float(), nullable=False),
            sa.Column('avg_view_seconds', sa.Float(), nullable=False),
            sa.Column('avg_view_pct', sa.Float(), nullable=False),
            sa.Column('subs_gained', sa.Integer(), nullable=False),
            sa.Column('revenue_usd', sa.Float(), nullable=True),
            sa.Column('ad_revenue_usd', sa.Float(), nullable=True),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('is_demo', sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('channel_id', 'youtube_video_id', name='uq_video_metric'))
        op.create_index('ix_video_metrics_channel_id', 'video_metrics', ['channel_id'])
        op.create_index('ix_video_metrics_youtube_video_id', 'video_metrics', ['youtube_video_id'])
    if "growth_plans" not in existing:
        op.create_table('growth_plans',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('month', sa.String(length=7), nullable=False),
            sa.Column('channel_id', sa.String(length=64), nullable=True),
            sa.Column('kpi_usd', sa.Float(), nullable=False),
            sa.Column('status', sa.JSON(), nullable=False),
            sa.Column('decisions', sa.JSON(), nullable=False),
            sa.Column('owner_actions', sa.JSON(), nullable=False),
            sa.Column('memo', sa.JSON(), nullable=True),
            sa.Column('applied', sa.Boolean(), nullable=False),
            sa.Column('actor', sa.String(length=40), nullable=False),
            sa.Column('is_demo', sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint('id'))
        op.create_index('ix_growth_plans_at', 'growth_plans', ['at'])
        op.create_index('ix_growth_plans_month', 'growth_plans', ['month'])


def downgrade() -> None:
    op.drop_table('growth_plans')
    op.drop_table('video_metrics')
    op.drop_table('channel_metric_days')
