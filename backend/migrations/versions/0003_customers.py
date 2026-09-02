"""Add customers table.

Revision ID: 0003_customers
Revises: 0002_license_number
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_customers"
down_revision = "0002_license_number"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("short_name", sa.String(60)),
        sa.Column("category", sa.String(40), nullable=False, server_default="Business"),
        sa.Column("country", sa.String(80), nullable=False, server_default="India"),
        sa.Column("gstin", sa.String(15)),
        sa.Column("pan", sa.String(10)),
        sa.Column("credit_limit", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("payment_terms", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "code", name="uq_customer_org_code"),
    )
    op.create_index("ix_customers_organization_id", "customers", ["organization_id"])
    op.create_index("ix_customers_status", "customers", ["status"])


def downgrade():
    op.drop_index("ix_customers_status", table_name="customers")
    op.drop_index("ix_customers_organization_id", table_name="customers")
    op.drop_table("customers")
