"""Containers planned when a sea FCL job is created (type and number of boxes)

Revision ID: 0065_planned_containers
Revises: 0064_notification_settings
"""
import sqlalchemy as sa
from alembic import op

revision = "0065_planned_containers"
down_revision = "0064_notification_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("planned_containers", sa.JSON()))


def downgrade() -> None:
    op.drop_column("shipments", "planned_containers")
