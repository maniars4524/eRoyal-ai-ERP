"""User profile JSON (designation, department, branch, ...).

Revision ID: 0008_user_profile
Revises: 0007_master_records
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_user_profile"
down_revision = "0007_master_records"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("profile", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("users", "profile")
