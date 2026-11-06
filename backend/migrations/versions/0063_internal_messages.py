"""Internal messages shown in the notification centre

Revision ID: 0063_internal_messages
Revises: 0062_job_output_docs
"""
import sqlalchemy as sa
from alembic import op

revision = "0063_internal_messages"
down_revision = "0062_job_output_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False, server_default=""),
        sa.Column("expires_on", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("internal_messages")
