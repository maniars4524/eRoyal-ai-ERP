"""Carrier bookings.

Revision ID: 0014_bookings
Revises: 0013_quotations
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_bookings"
down_revision = "0013_quotations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="Sea"),
        sa.Column("direction", sa.String(20), nullable=False, server_default="Export"),
        sa.Column("shipment_type", sa.String(40)),
        sa.Column("booking_type", sa.String(20)),
        sa.Column("hbl_type", sa.String(60)),
        sa.Column("scac", sa.String(20)),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("party_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("carrier", sa.String(160)),
        sa.Column("carrier_booking_no", sa.String(80)),
        sa.Column("vessel", sa.String(120)),
        sa.Column("pol", sa.String(80)),
        sa.Column("pod", sa.String(80)),
        sa.Column("etd", sa.Date()),
        sa.Column("eta", sa.Date()),
        sa.Column("si_cutoff", sa.Date()),
        sa.Column("vgm_cutoff", sa.Date()),
        sa.Column("job_number", sa.String(40)),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "number", name="uq_booking_number"),
    )
    for col in ("organization_id", "status", "customer_id", "job_number"):
        op.create_index(f"ix_bookings_{col}", "bookings", [col])


def downgrade():
    op.drop_table("bookings")
