"""Job numbers are unique per company (not globally).

Revision ID: 0015_job_number_per_org
Revises: 0014_bookings
"""
from alembic import op

revision = "0015_job_number_per_org"
down_revision = "0014_bookings"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("shipments_job_number_key", "shipments", type_="unique")
    op.create_index("ix_shipments_job_number", "shipments", ["job_number"])
    op.create_unique_constraint("uq_shipment_org_job", "shipments", ["organization_id", "job_number"])


def downgrade():
    op.drop_constraint("uq_shipment_org_job", "shipments", type_="unique")
    op.drop_index("ix_shipments_job_number", table_name="shipments")
    op.create_unique_constraint("shipments_job_number_key", "shipments", ["job_number"])
