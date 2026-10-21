"""transaction details of receipts and payments (cheque, UTR, bank, full / part settlement)

Revision ID: 0032_payment_details
Revises: 0031_job_impdocs
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_payment_details"
down_revision = "0031_job_impdocs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("details", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("payments", "details")
