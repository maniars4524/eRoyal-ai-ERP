"""Job routing: flights (air) and vessel legs (sea) of a job, with a change history

Revision ID: 0061_job_routing
Revises: 0060_job_cancel_protect
"""
import sqlalchemy as sa
from alembic import op

revision = "0061_job_routing"
down_revision = "0060_job_cancel_protect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_routing_legs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("carrier", sa.String(120)),
        sa.Column("voyage", sa.String(40)),
        sa.Column("pol", sa.String(80)),
        sa.Column("pod", sa.String(80)),
        sa.Column("etd", sa.DateTime(timezone=True)),
        sa.Column("eta", sa.DateTime(timezone=True)),
        sa.Column("stage", sa.String(10), nullable=False, server_default="PLANNED"),
        sa.Column("follow_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("follow_up_status", sa.String(10), nullable=False, server_default="OPEN"),
        sa.Column("follow_up_note", sa.String(500)),
        sa.Column("remarks", sa.String(500)),
        sa.Column("source", sa.String(10), nullable=False, server_default="MANUAL"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "job_routing_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("leg_id", sa.Integer(), sa.ForeignKey("job_routing_legs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("field", sa.String(30)),
        sa.Column("old_value", sa.String(300)),
        sa.Column("new_value", sa.String(300)),
        sa.Column("remarks", sa.String(500)),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("job_routing_history")
    op.drop_table("job_routing_legs")
