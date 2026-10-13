"""Quotations and quotation lines.

Revision ID: 0013_quotations
Revises: 0012_party_roles
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_quotations"
down_revision = "0012_party_roles"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(16, 2)


def upgrade():
    op.create_table(
        "quotations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("quote_date", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date()),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("party_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("mode", sa.String(20), nullable=False, server_default="Sea"),
        sa.Column("direction", sa.String(20), nullable=False, server_default="Export"),
        sa.Column("shipment_type", sa.String(40)),
        sa.Column("quote_type", sa.String(60)),
        sa.Column("carrier_mode", sa.String(10), nullable=False, server_default="single"),
        sa.Column("pol", sa.String(80)),
        sa.Column("pod", sa.String(80)),
        sa.Column("terms", sa.Text()),
        sa.Column("remarks", sa.Text()),
        sa.Column("header", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("job_number", sa.String(40)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "number", name="uq_quotation_number"),
    )
    for col in ("organization_id", "status", "customer_id"):
        op.create_index(f"ix_quotations_{col}", "quotations", [col])
    op.create_table(
        "quotation_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quotation_id", sa.Integer(), sa.ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("charge_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("carrier", sa.String(160), nullable=False, server_default=""),
        sa.Column("basis", sa.String(40), nullable=False, server_default="Per Shipment"),
        sa.Column("ccy", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("exch", sa.Numeric(14, 6), nullable=False, server_default="1"),
        sa.Column("rate", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, server_default="1"),
        sa.Column("tax_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.UniqueConstraint("quotation_id", "line_no", name="uq_quotation_line"),
    )
    op.create_index("ix_quotation_lines_quotation_id", "quotation_lines", ["quotation_id"])


def downgrade():
    op.drop_table("quotation_lines")
    op.drop_table("quotations")
