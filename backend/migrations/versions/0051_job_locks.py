"""Job locking: one editor per job (lease with heartbeat), access requests, audit events

Revision ID: 0051_job_locks
Revises: 0050_cha_files
"""
import sqlalchemy as sa
from alembic import op

revision = "0051_job_locks"
down_revision = "0050_cha_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_number", sa.String(60), nullable=False),
        sa.Column("job_type", sa.String(30), nullable=False, server_default="Other"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_name", sa.String(160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_activity", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("tabs", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("organization_id", "job_number", name="uq_job_locks_job"),
    )
    op.create_table(
        "job_lock_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_number", sa.String(60), nullable=False, index=True),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requester_name", sa.String(160), nullable=False),
        sa.Column("holder_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "job_lock_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_number", sa.String(60), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("event", sa.String(30), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("notify_user_id", sa.Integer(), nullable=True, index=True),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_lock_events")
    op.drop_table("job_lock_requests")
    op.drop_table("job_locks")
