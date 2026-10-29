"""Basis-based charges: the percentage of a % basis line and a flag for a quantity typed by the user

Revision ID: 0052_charge_basis
Revises: 0051_job_locks
"""
import sqlalchemy as sa
from alembic import op

revision = "0052_charge_basis"
down_revision = "0051_job_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_charges", sa.Column("buy_pct", sa.Numeric(8, 3), nullable=True))
    op.add_column("job_charges", sa.Column("sell_pct", sa.Numeric(8, 3), nullable=True))
    op.add_column("job_charges", sa.Column("qty_manual", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("job_charges", "qty_manual")
    op.drop_column("job_charges", "sell_pct")
    op.drop_column("job_charges", "buy_pct")
