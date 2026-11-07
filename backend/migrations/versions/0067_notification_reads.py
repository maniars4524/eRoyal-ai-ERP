"""Which notifications each user has read

Revision ID: 0067_notification_reads
Revises: 0066_charge_dr_cr
"""
import sqlalchemy as sa
from alembic import op

revision = "0067_notification_reads"
down_revision = "0066_charge_dr_cr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item_id", sa.String(160), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "item_id", name="uq_notification_read"),
    )


def downgrade() -> None:
    op.drop_table("notification_reads")
