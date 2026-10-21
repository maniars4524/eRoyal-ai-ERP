"""a document line can belong to a job other than the document's own (one invoice for several jobs of a party)

Revision ID: 0034_line_job
Revises: 0033_allocation_tds
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_line_job"
down_revision = "0033_allocation_tds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_lines", sa.Column("job_number", sa.String(40), nullable=True))
    op.create_index("ix_document_lines_job_number", "document_lines", ["job_number"])
    op.add_column("payment_allocations", sa.Column("round_off", sa.Numeric(16, 2), nullable=False, server_default="0"))


def downgrade():
    op.drop_index("ix_document_lines_job_number", "document_lines")
    op.drop_column("document_lines", "job_number")
    op.drop_column("payment_allocations", "round_off")
