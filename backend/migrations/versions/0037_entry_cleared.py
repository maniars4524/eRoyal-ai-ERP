"""bank reconciliation: the date a bank entry cleared in the bank statement

Revision ID: 0037_entry_cleared
Revises: 0036_gl_vouchers
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_entry_cleared"
down_revision = "0036_gl_vouchers"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("acc_entries", sa.Column("cleared_date", sa.Date, nullable=True))


def downgrade():
    op.drop_column("acc_entries", "cleared_date")
