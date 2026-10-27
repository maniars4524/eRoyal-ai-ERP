"""CHA jobs: the customs-specific details of a job (file reference, customs location, CHA licence, MBL / HBL, containers, parties of a stand-alone job)

Revision ID: 0049_cha_job_info
Revises: 0048_cha_jobs_link
"""
import sqlalchemy as sa
from alembic import op

revision = "0049_cha_job_info"
down_revision = "0048_cha_jobs_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("cha_info", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shipments", "cha_info")
