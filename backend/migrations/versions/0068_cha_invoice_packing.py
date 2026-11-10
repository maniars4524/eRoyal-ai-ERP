"""CHA: commercial invoice and packing list prepared on behalf of the shipper

Revision ID: 0068_cha_invoice_packing
Revises: 0067_notification_reads
"""
import sqlalchemy as sa
from alembic import op

revision = "0068_cha_invoice_packing"
down_revision = "0067_notification_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cha_invoice_packing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("invoice_no", sa.String(60), nullable=False),
        sa.Column("invoice_date", sa.Date()),
        sa.Column("job_number", sa.String(40)),
        sa.Column("exporter", sa.String(200), server_default=""),
        sa.Column("consignee", sa.String(200), server_default=""),
        sa.Column("currency", sa.String(3), server_default="USD"),
        sa.Column("total", sa.Numeric(16, 2), server_default="0"),
        sa.Column("data", sa.JSON()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cha_invpl_org_invoice", "cha_invoice_packing", ["organization_id", "invoice_no"])


def downgrade() -> None:
    op.drop_index("ix_cha_invpl_org_invoice", table_name="cha_invoice_packing")
    op.drop_table("cha_invoice_packing")
