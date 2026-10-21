"""GSTR-2B rows imported from the GST portal, for the ITC reconciliation

Revision ID: 0040_gstr2b
Revises: 0039_fy_closings
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_gstr2b"
down_revision = "0039_fy_closings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gstr2b_rows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period", sa.String(7), nullable=False, index=True),
        sa.Column("gstin", sa.String(15), nullable=False),
        sa.Column("supplier", sa.String(200)),
        sa.Column("doc_kind", sa.String(10), nullable=False, server_default="INV"),
        sa.Column("inv_no", sa.String(60), nullable=False),
        sa.Column("inv_date", sa.Date),
        sa.Column("taxable", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("cgst", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("sgst", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("igst", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("itc_available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("imported_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("gstr2b_rows")
