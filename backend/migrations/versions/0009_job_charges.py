"""Job charge lines (buy/sell) per shipment.

Revision ID: 0009_job_charges
Revises: 0008_user_profile
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_job_charges"
down_revision = "0008_user_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_charges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("charge_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("description", sa.String(240), nullable=False, server_default=""),
        sa.Column("uom", sa.String(40), nullable=False, server_default="Per Shipment"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, server_default="1"),
        sa.Column("hsn", sa.String(20), nullable=False, server_default=""),
        sa.Column("buy_ccy", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("buy_exch", sa.Numeric(14, 6), nullable=False, server_default="1"),
        sa.Column("buy_rate", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("buy_tax_type", sa.String(20), nullable=False, server_default="Taxable"),
        sa.Column("buy_tax_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("vendor", sa.String(160), nullable=False, server_default=""),
        sa.Column("use_vendor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sell_ccy", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("sell_exch", sa.Numeric(14, 6), nullable=False, server_default="1"),
        sa.Column("sell_rate", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("sell_tax_type", sa.String(20), nullable=False, server_default="Taxable"),
        sa.Column("sell_tax_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("client", sa.String(160), nullable=False, server_default=""),
        sa.Column("use_client", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("shipment_id", "line_no", name="uq_job_charge_line"),
    )
    op.create_index("ix_job_charges_organization_id", "job_charges", ["organization_id"])
    op.create_index("ix_job_charges_shipment_id", "job_charges", ["shipment_id"])


def downgrade():
    op.drop_table("job_charges")
