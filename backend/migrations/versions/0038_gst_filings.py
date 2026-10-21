"""GST return filing status per period (GSTR-1 / GSTR-3B)

Revision ID: 0038_gst_filings
Revises: 0037_entry_cleared
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_gst_filings"
down_revision = "0037_entry_cleared"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gst_filings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("return_type", sa.String(10), nullable=False),
        sa.Column("filed_on", sa.Date, nullable=False),
        sa.Column("ack_no", sa.String(40)),
        sa.Column("filed_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "period", "return_type", name="uq_gst_filing"),
    )


def downgrade():
    op.drop_table("gst_filings")
