"""customs clearance bills (SB / BE, several per booking) and the service of every job charge

Revision ID: 0028_customs_bills
Revises: 0027_simple_job_stages
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_customs_bills"
down_revision = "0027_simple_job_stages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customs_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("booking_id", sa.Integer(), sa.ForeignKey("bookings.id", ondelete="SET NULL")),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bill_type", sa.String(2), nullable=False),
        sa.Column("bill_no", sa.String(40), nullable=False),
        sa.Column("bill_date", sa.Date()),
        sa.Column("reference", sa.String(120)),
        sa.Column("port_code", sa.String(20)),
        sa.Column("assessable_value", sa.Numeric(14, 2), server_default="0"),
        sa.Column("duty_amount", sa.Numeric(14, 2), server_default="0"),
        sa.Column("examination", sa.String(40)),
        sa.Column("ooc_date", sa.Date()),
        sa.Column("status", sa.String(20), server_default="Filed", nullable=False),
        sa.Column("remarks", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "bill_type", "bill_no", name="uq_customs_bill_no"),
    )
    op.create_index("ix_customs_bills_org", "customs_bills", ["organization_id"])
    op.create_index("ix_customs_bills_booking", "customs_bills", ["booking_id"])
    op.create_index("ix_customs_bills_shipment", "customs_bills", ["shipment_id"])
    op.add_column("job_charges", sa.Column("service", sa.String(20), server_default="Freight", nullable=False))


def downgrade():
    op.drop_column("job_charges", "service")
    op.drop_table("customs_bills")
