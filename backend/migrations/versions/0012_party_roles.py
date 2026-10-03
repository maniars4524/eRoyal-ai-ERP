"""Organization master (customers table) becomes the party master: roles + full form data.

Revision ID: 0012_party_roles
Revises: 0011_allocation_fk
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_party_roles"
down_revision = "0011_allocation_fk"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customers", sa.Column("roles", sa.JSON(), nullable=False, server_default='["shipper"]'))
    op.add_column("customers", sa.Column("data", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("customers", "data")
    op.drop_column("customers", "roles")
