"""jobs carry their branch and salesperson (Branch and Salesperson MIS)

Revision ID: 0043_job_branch_salesperson
Revises: 0042_network_masters
"""
import sqlalchemy as sa
from alembic import op

revision = "0043_job_branch_salesperson"
down_revision = "0042_network_masters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("branch", sa.String(80), nullable=True))
    op.add_column("shipments", sa.Column("salesperson", sa.String(120), nullable=True))
    op.create_index("ix_shipments_branch", "shipments", ["branch"])
    op.create_index("ix_shipments_salesperson", "shipments", ["salesperson"])


def downgrade() -> None:
    op.drop_index("ix_shipments_salesperson", table_name="shipments")
    op.drop_index("ix_shipments_branch", table_name="shipments")
    op.drop_column("shipments", "salesperson")
    op.drop_column("shipments", "branch")
