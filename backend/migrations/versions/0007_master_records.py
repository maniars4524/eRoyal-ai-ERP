"""Generic tenant-scoped master data table.

Revision ID: 0007_master_records
Revises: 0006_portals_lockout
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_master_records"
down_revision = "0006_portals_lockout"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "master_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("master_type", sa.String(40), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "master_type", "code", name="uq_master_org_type_code"),
    )
    op.create_index("ix_master_records_organization_id", "master_records", ["organization_id"])
    op.create_index("ix_master_records_master_type", "master_records", ["master_type"])
    op.create_index("ix_master_records_status", "master_records", ["status"])


def downgrade():
    op.drop_table("master_records")
