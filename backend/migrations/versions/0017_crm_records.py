"""CRM records (leads, enquiries, opportunities, follow-ups, CRM bookings) persisted per company.

Revision ID: 0017_crm_records
Revises: 0016_quote_sell_buy
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_crm_records"
down_revision = "0016_quote_sell_buy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref", sa.String(40), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(40), nullable=False, server_default=""),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "kind", "ref", name="uq_crm_record_ref"),
    )
    op.create_index("ix_crm_records_organization_id", "crm_records", ["organization_id"])
    op.create_index("ix_crm_records_kind", "crm_records", ["kind"])
    op.create_index("ix_crm_records_customer_id", "crm_records", ["customer_id"])


def downgrade():
    op.drop_table("crm_records")
