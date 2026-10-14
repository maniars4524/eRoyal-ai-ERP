"""Company logo for printed documents (stored as a small validated image data URL).

Revision ID: 0023_company_logo
Revises: 0022_currency_master_rules
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_company_logo"
down_revision = "0022_currency_master_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("logo", sa.Text()))


def downgrade():
    op.drop_column("organizations", "logo")
