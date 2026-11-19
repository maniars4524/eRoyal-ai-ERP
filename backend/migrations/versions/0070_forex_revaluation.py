"""Forex: invoice-wise revaluation of open foreign currency balances

Revision ID: 0070_forex_revaluation
Revises: 0069_forex_settlement
"""
import sqlalchemy as sa
from alembic import op

revision = "0070_forex_revaluation"
down_revision = "0069_forex_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forex_revaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("as_of", sa.Date(), nullable=False, index=True),
        sa.Column("ccy", sa.String(3), nullable=False),
        sa.Column("fc_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("closing_rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("carrying_inr", sa.Numeric(16, 2), nullable=False),
        sa.Column("revalued_inr", sa.Numeric(16, 2), nullable=False),
        sa.Column("difference", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("voucher_id", sa.Integer()),
        sa.Column("reversal_voucher_id", sa.Integer()),
        sa.Column("status", sa.String(12), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_forex_reval_doc_date", "forex_revaluations", ["document_id", "as_of"])


def downgrade() -> None:
    op.drop_table("forex_revaluations")
