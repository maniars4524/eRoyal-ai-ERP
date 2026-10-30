"""Invoice lines keep their container-wise breakup; Basis % lines remember which charges their percentage is taken from

Revision ID: 0054_breakup_and_pct_base
Revises: 0053_quote_line_qty_manual
"""
import sqlalchemy as sa
from alembic import op

revision = "0054_breakup_and_pct_base"
down_revision = "0053_quote_line_qty_manual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_lines", sa.Column("breakup", sa.JSON(), nullable=True))
    op.add_column("quotation_lines", sa.Column("pct_base", sa.JSON(), nullable=True))
    op.add_column("job_charges", sa.Column("pct_base", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_charges", "pct_base")
    op.drop_column("quotation_lines", "pct_base")
    op.drop_column("document_lines", "breakup")
