"""Demo tenant flag and account lockout columns.

Revision ID: 0006_portals_lockout
Revises: 0005_shipment_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_portals_lockout"
down_revision = "0005_shipment_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_column("organizations", "is_demo")
