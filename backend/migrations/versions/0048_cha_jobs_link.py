"""CHA (customs) jobs: explicit link to the parent freight job and the customs status of the job

Revision ID: 0048_cha_jobs_link
Revises: 0047_hbl_clauses
"""
import sqlalchemy as sa
from alembic import op

revision = "0048_cha_jobs_link"
down_revision = "0047_hbl_clauses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("parent_job_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_shipments_parent_job_id", "shipments", ["parent_job_id"])
    op.add_column("shipments", sa.Column("customs_status", sa.String(30), nullable=True))
    # the customs jobs that already exist: each is linked to the freight job of its booking (when the booking has one), and starts at the first customs stage
    op.execute("""
        UPDATE shipments c SET parent_job_id = (
            SELECT f.id FROM shipments f WHERE f.organization_id = c.organization_id AND f.booking_id = c.booking_id AND f.job_type = 'Forwarding' ORDER BY f.id LIMIT 1)
        WHERE c.job_type = 'Customs' AND c.booking_id IS NOT NULL AND c.parent_job_id IS NULL
    """)
    op.execute("UPDATE shipments SET customs_status = 'CREATED' WHERE job_type = 'Customs' AND customs_status IS NULL")


def downgrade() -> None:
    op.drop_index("ix_shipments_parent_job_id", table_name="shipments")
    op.drop_column("shipments", "customs_status")
    op.drop_column("shipments", "parent_job_id")
