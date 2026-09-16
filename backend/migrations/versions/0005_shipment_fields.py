"""Extend shipments with real job fields.

Revision ID: 0005_shipment_fields
Revises: 0004_ports
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_shipment_fields"
down_revision = "0004_ports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shipments", sa.Column("job_type", sa.String(20), nullable=False, server_default="Forwarding"))
    op.add_column("shipments", sa.Column("container_mode", sa.String(20)))
    op.add_column("shipments", sa.Column("department", sa.String(20)))
    op.add_column("shipments", sa.Column("pol", sa.String(80)))
    op.add_column("shipments", sa.Column("pod", sa.String(80)))
    op.add_column("shipments", sa.Column("etd", sa.Date()))
    op.add_column("shipments", sa.Column("eta", sa.Date()))
    op.add_column("shipments", sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="SET NULL")))
    op.add_column("shipments", sa.Column("consignee", sa.String(160)))
    op.add_column("shipments", sa.Column("packages", sa.Integer()))
    op.add_column("shipments", sa.Column("package_type", sa.String(40)))
    op.add_column("shipments", sa.Column("gross_weight", sa.Numeric(12, 3)))
    op.add_column("shipments", sa.Column("volume", sa.Numeric(12, 3)))
    op.add_column("shipments", sa.Column("commodity", sa.String(160)))
    op.add_column("shipments", sa.Column("bl_awb", sa.String(60)))
    op.add_column("shipments", sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("shipments", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_index("ix_shipments_customer_id", "shipments", ["customer_id"])


def downgrade():
    op.drop_index("ix_shipments_customer_id", table_name="shipments")
    for col in ("updated_at", "created_by", "bl_awb", "commodity", "volume", "gross_weight", "package_type", "packages", "consignee", "customer_id", "eta", "etd", "pod", "pol", "department", "container_mode", "job_type"):
        op.drop_column("shipments", col)
