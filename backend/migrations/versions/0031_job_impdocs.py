"""import document details (Delivery Order number, validity, IGM, delivery type) saved per job

Revision ID: 0031_job_impdocs
Revises: 0030_company_doc_profile
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_job_impdocs"
down_revision = "0030_company_doc_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_details", sa.Column("impdocs", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("job_details", "impdocs")
