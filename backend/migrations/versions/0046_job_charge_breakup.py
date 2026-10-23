"""job charges: optional container-wise break-up of a charge (type, quantity, sell rate, buy rate)

Revision ID: 0046_job_charge_breakup
Revises: 0045_license_contracts_usage
"""
import sqlalchemy as sa
from alembic import op

revision = "0046_job_charge_breakup"
down_revision = "0045_license_contracts_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_charges", sa.Column("breakup", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_charges", "breakup")
