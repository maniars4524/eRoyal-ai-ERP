"""accounting vouchers and entries (the general ledger)

Revision ID: 0036_gl_vouchers
Revises: 0035_chart_of_accounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_gl_vouchers"
down_revision = "0035_chart_of_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "acc_vouchers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("voucher_type", sa.String(24), nullable=False, index=True),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("vdate", sa.Date, nullable=False, index=True),
        sa.Column("narration", sa.String(400)),
        sa.Column("party_name", sa.String(200)),
        sa.Column("job_number", sa.String(40)),
        sa.Column("source_type", sa.String(20)),
        sa.Column("source_id", sa.Integer, index=True),
        sa.Column("total", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="POSTED", index=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "voucher_type", "number", name="uq_accvoucher_number"),
    )
    op.create_table(
        "acc_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("voucher_id", sa.Integer, sa.ForeignKey("acc_vouchers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.Integer, sa.ForeignKey("acc_ledgers.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("debit", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("narration", sa.String(240)),
        sa.Column("job_number", sa.String(40)),
    )


def downgrade():
    op.drop_table("acc_entries")
    op.drop_table("acc_vouchers")
