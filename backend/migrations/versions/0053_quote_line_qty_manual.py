"""Quotation lines: a flag for a quantity typed by the user (a Basis does not overwrite it)

Revision ID: 0053_quote_line_qty_manual
Revises: 0052_charge_basis
"""
import sqlalchemy as sa
from alembic import op

revision = "0053_quote_line_qty_manual"
down_revision = "0052_charge_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quotation_lines", sa.Column("qty_manual", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("quotation_lines", "qty_manual")
