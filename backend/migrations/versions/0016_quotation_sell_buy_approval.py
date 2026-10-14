"""Quotations: separate sell/buy cost lines, container-wise rates, approval workflow, revisions, audit events.

Existing quotations are preserved: every existing line becomes a SELL line, non-draft quotations are marked APPROVED,
and every quotation becomes the root of its own revision chain.

Revision ID: 0016_quote_sell_buy
Revises: 0015_job_number_per_org
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_quote_sell_buy"
down_revision = "0015_job_number_per_org"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(16, 2)


def upgrade():
    # ---- quotation header: approval + revisions
    op.add_column("quotations", sa.Column("approval_status", sa.String(12), nullable=False, server_default="DRAFT"))
    op.add_column("quotations", sa.Column("prepared_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("quotations", sa.Column("prepared_at", sa.DateTime(timezone=True)))
    op.add_column("quotations", sa.Column("submitted_at", sa.DateTime(timezone=True)))
    op.add_column("quotations", sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("quotations", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("quotations", sa.Column("approval_remarks", sa.Text()))
    op.add_column("quotations", sa.Column("root_id", sa.Integer()))
    op.add_column("quotations", sa.Column("revision_no", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_quotations_root_id", "quotations", ["root_id"])
    op.execute("UPDATE quotations SET root_id = id, prepared_by = created_by, prepared_at = created_at")
    op.execute("UPDATE quotations SET approval_status = 'APPROVED' WHERE status <> 'DRAFT'")

    # ---- quotation lines: sell/buy side, tax treatment, container rates, remarks, stored amounts
    op.add_column("quotation_lines", sa.Column("side", sa.String(4), nullable=False, server_default="SELL"))
    op.add_column("quotation_lines", sa.Column("taxable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("quotation_lines", sa.Column("container_type", sa.String(40)))
    op.add_column("quotation_lines", sa.Column("container_breakup", sa.JSON()))
    op.add_column("quotation_lines", sa.Column("remarks", sa.String(500), nullable=False, server_default=""))
    op.add_column("quotation_lines", sa.Column("amount_fcy", MONEY, nullable=False, server_default="0"))
    op.add_column("quotation_lines", sa.Column("gst_amount", MONEY, nullable=False, server_default="0"))
    op.add_column("quotation_lines", sa.Column("total_inr", MONEY, nullable=False, server_default="0"))
    op.execute("UPDATE quotation_lines SET taxable = (tax_pct > 0), amount_fcy = ROUND(qty * rate, 2), "
               "gst_amount = ROUND(amount * tax_pct / 100, 2), total_inr = amount + ROUND(amount * tax_pct / 100, 2)")

    # ---- audit trail of every workflow step
    op.create_table(
        "quotation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quotation_id", sa.Integer(), sa.ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("from_state", sa.String(30)),
        sa.Column("to_state", sa.String(30)),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("remarks", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_quotation_events_quotation_id", "quotation_events", ["quotation_id"])


def downgrade():
    op.drop_table("quotation_events")
    for col in ("total_inr", "gst_amount", "amount_fcy", "remarks", "container_breakup", "container_type", "taxable", "side"):
        op.drop_column("quotation_lines", col)
    op.drop_index("ix_quotations_root_id", table_name="quotations")
    for col in ("revision_no", "root_id", "approval_remarks", "approved_at", "approved_by", "submitted_at", "prepared_at", "prepared_by", "approval_status"):
        op.drop_column("quotations", col)
