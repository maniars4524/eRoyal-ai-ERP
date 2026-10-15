"""jobs belong to a booking: one booking number, several jobs (freight, consol, CHA, transport ...)

Revision ID: 0026_job_booking_link
Revises: 0025_booking_quotation_link
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_job_booking_link"
down_revision = "0025_booking_quotation_link"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shipments", sa.Column("booking_id", sa.Integer(), sa.ForeignKey("bookings.id", ondelete="SET NULL")))
    op.create_index("ix_shipments_booking_id", "shipments", ["booking_id"])
    # jobs that were already created from a confirmed booking keep their link
    op.execute("UPDATE shipments s SET booking_id = b.id FROM bookings b WHERE b.job_number = s.job_number AND b.organization_id = s.organization_id")


def downgrade():
    op.drop_index("ix_shipments_booking_id", table_name="shipments")
    op.drop_column("shipments", "booking_id")
