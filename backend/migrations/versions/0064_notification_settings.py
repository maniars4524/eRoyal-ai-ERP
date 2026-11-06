"""Notification settings per company, and an audience for internal messages

Revision ID: 0064_notification_settings
Revises: 0063_internal_messages
"""
import sqlalchemy as sa
from alembic import op

revision = "0064_notification_settings"
down_revision = "0063_internal_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("internal_messages", sa.Column("audience_roles", sa.JSON()))


def downgrade() -> None:
    op.drop_column("internal_messages", "audience_roles")
    op.drop_table("notification_settings")
