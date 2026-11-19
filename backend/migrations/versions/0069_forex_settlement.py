"""Forex: settlement rate and realised gain / loss on receipt and payment allocations

Revision ID: 0069_forex_settlement
Revises: 0068_cha_invoice_packing
"""
import sqlalchemy as sa
from alembic import op

revision = "0069_forex_settlement"
down_revision = "0068_cha_invoice_packing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_allocations", sa.Column("fx_amount", sa.Numeric(16, 2)))
    op.add_column("payment_allocations", sa.Column("fx_rate", sa.Numeric(18, 6)))
    op.add_column("payment_allocations", sa.Column("fx_cash", sa.Numeric(16, 2)))
    op.add_column("payment_allocations", sa.Column("fx_gain", sa.Numeric(16, 2), server_default="0"))


def downgrade() -> None:
    for c in ("fx_gain", "fx_cash", "fx_rate", "fx_amount"):
        op.drop_column("payment_allocations", c)
