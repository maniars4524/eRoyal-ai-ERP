"""financial year closings

Revision ID: 0039_fy_closings
Revises: 0038_gst_filings
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_fy_closings"
down_revision = "0038_gst_filings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fy_closings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("fy_start", sa.Integer, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("net_profit", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("voucher_id", sa.Integer, sa.ForeignKey("acc_vouchers.id", ondelete="SET NULL")),
        sa.Column("closed_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "fy_start", name="uq_fy_closing"),
    )


def downgrade():
    op.drop_table("fy_closings")
