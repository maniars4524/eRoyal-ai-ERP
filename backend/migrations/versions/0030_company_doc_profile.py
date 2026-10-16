"""company document profile (letterhead, registration numbers, signature and seal) for printed BL / MTD

Revision ID: 0030_company_doc_profile
Revises: 0029_job_details
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_company_doc_profile"
down_revision = "0029_job_details"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "company_doc_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("company_doc_profiles")
