"""Allocation -> document FK: NO ACTION (still blocks deleting a paid document, but lets a tenant cascade-delete cleanly).

Revision ID: 0011_allocation_fk
Revises: 0010_documents_payments
"""
from alembic import op

revision = "0011_allocation_fk"
down_revision = "0010_documents_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("payment_allocations_document_id_fkey", "payment_allocations", type_="foreignkey")
    op.create_foreign_key("payment_allocations_document_id_fkey", "payment_allocations", "documents", ["document_id"], ["id"])


def downgrade():
    op.drop_constraint("payment_allocations_document_id_fkey", "payment_allocations", type_="foreignkey")
    op.create_foreign_key("payment_allocations_document_id_fkey", "payment_allocations", "documents", ["document_id"], ["id"], ondelete="RESTRICT")
