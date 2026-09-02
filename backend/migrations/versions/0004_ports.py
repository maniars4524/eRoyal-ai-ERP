"""Add global ports reference table.

Revision ID: 0004_ports
Revises: 0003_customers
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_ports"
down_revision = "0003_customers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("unlocode", sa.String(10)),
        sa.Column("port_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("city", sa.String(80)),
        sa.Column("country", sa.String(80), nullable=False),
        sa.Column("timezone", sa.String(60)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ports_code", "ports", ["code"])
    op.create_index("ix_ports_status", "ports", ["status"])


def downgrade():
    op.drop_index("ix_ports_status", table_name="ports")
    op.drop_index("ix_ports_code", table_name="ports")
    op.drop_table("ports")
