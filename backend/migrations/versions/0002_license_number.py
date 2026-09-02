"""Add organization license_number.

Revision ID: 0002_license_number
Revises: 0001_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_license_number"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("license_number", sa.String(20)))
    op.create_unique_constraint("uq_organizations_license_number", "organizations", ["license_number"])
    op.create_index("ix_organizations_license_number", "organizations", ["license_number"])


def downgrade():
    op.drop_index("ix_organizations_license_number", table_name="organizations")
    op.drop_constraint("uq_organizations_license_number", "organizations", type_="unique")
    op.drop_column("organizations", "license_number")
