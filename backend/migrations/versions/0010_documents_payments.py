"""Billing documents, numbering sequences, payments and allocations.

Revision ID: 0010_documents_payments
Revises: 0009_job_charges
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_documents_payments"
down_revision = "0009_job_charges"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(16, 2)


def upgrade():
    op.create_table(
        "doc_sequences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq_type", sa.String(30), nullable=False),
        sa.Column("fiscal_year", sa.String(7), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("organization_id", "seq_type", "fiscal_year", name="uq_doc_sequence"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("doc_number", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date()),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="SET NULL")),
        sa.Column("job_number", sa.String(40)),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("party_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("party_gstin", sa.String(15)),
        sa.Column("place_of_supply", sa.String(60)),
        sa.Column("supply_type", sa.String(10), nullable=False, server_default="inter"),
        sa.Column("ref_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("remarks", sa.Text()),
        sa.Column("header", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("subtotal", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_total", MONEY, nullable=False, server_default="0"),
        sa.Column("grand_total", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("credited_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("posted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "doc_type", "doc_number", name="uq_document_number"),
        sa.CheckConstraint("paid_amount >= 0 AND credited_amount >= 0", name="ck_document_amounts_nonneg"),
    )
    for col in ("organization_id", "doc_type", "status", "shipment_id", "customer_id"):
        op.create_index(f"ix_documents_{col}", "documents", [col])
    op.create_table(
        "document_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("charge_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("description", sa.String(240), nullable=False, server_default=""),
        sa.Column("hsn", sa.String(20), nullable=False, server_default=""),
        sa.Column("uom", sa.String(40), nullable=False, server_default="Per Shipment"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, server_default="1"),
        sa.Column("ccy", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("exch", sa.Numeric(14, 6), nullable=False, server_default="1"),
        sa.Column("rate", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("tax_type", sa.String(20), nullable=False, server_default="Taxable"),
        sa.Column("tax_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("line_total", MONEY, nullable=False, server_default="0"),
        sa.UniqueConstraint("document_id", "line_no", name="uq_document_line"),
    )
    op.create_index("ix_document_lines_document_id", "document_lines", ["document_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("pay_number", sa.String(40), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("party_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("method", sa.String(30), nullable=False, server_default="Bank Transfer"),
        sa.Column("reference", sa.String(80)),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="POSTED"),
        sa.Column("idempotency_key", sa.String(80)),
        sa.Column("remarks", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "pay_number", name="uq_payment_number"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_idempotency"),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
    )
    op.create_index("ix_payments_organization_id", "payments", ["organization_id"])
    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_allocation_positive"),
    )
    op.create_index("ix_payment_allocations_payment_id", "payment_allocations", ["payment_id"])
    op.create_index("ix_payment_allocations_document_id", "payment_allocations", ["document_id"])


def downgrade():
    for table in ("payment_allocations", "payments", "document_lines", "documents", "doc_sequences"):
        op.drop_table(table)
