"""tax deducted at source on receipts: per allocation TDS amount

Revision ID: 0033_allocation_tds
Revises: 0032_payment_details
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_allocation_tds"
down_revision = "0032_payment_details"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_allocations", sa.Column("tds_amount", sa.Numeric(16, 2), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("payment_allocations", "tds_amount")
