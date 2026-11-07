"""DR / CR on every job charge line (DR adds, CR deducts), for settling with an overseas agent

Revision ID: 0066_charge_dr_cr
Revises: 0065_planned_containers
"""
import sqlalchemy as sa
from alembic import op

revision = "0066_charge_dr_cr"
down_revision = "0065_planned_containers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_charges", sa.Column("dr_cr", sa.String(2), nullable=False, server_default="DR"))


def downgrade() -> None:
    op.drop_column("job_charges", "dr_cr")
