"""bookings can be raised from a quotation

Revision ID: 0025_booking_quotation_link
Revises: 0024_single_session
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_booking_quotation_link"
down_revision = "0024_single_session"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bookings", sa.Column("quotation_id", sa.Integer(), sa.ForeignKey("quotations.id", ondelete="SET NULL")))
    op.create_index("ix_bookings_quotation_id", "bookings", ["quotation_id"])


def downgrade():
    op.drop_index("ix_bookings_quotation_id", table_name="bookings")
    op.drop_column("bookings", "quotation_id")
