"""full_auto — Tự động hoàn toàn: hẹn giờ tự công khai, nút Dừng, số lần tự sửa lỗi

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("stories")}
    with op.batch_alter_table("stories") as b:
        if "publish_after" not in cols:
            b.add_column(sa.Column("publish_after", sa.DateTime(timezone=True), nullable=True))
        if "publish_hold" not in cols:
            b.add_column(sa.Column("publish_hold", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "auto_heal_attempts" not in cols:
            b.add_column(sa.Column("auto_heal_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("stories") as b:
        b.drop_column("auto_heal_attempts")
        b.drop_column("publish_hold")
        b.drop_column("publish_after")
