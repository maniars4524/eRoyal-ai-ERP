"""saved job details (parties, cargo, containers, air dimensions, BL) and the House BL running numbers

Revision ID: 0029_job_details
Revises: 0028_customs_bills
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_job_details"
down_revision = "0028_customs_bills"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_details",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("parties", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cargo", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("containers", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dims", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("bl", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("saved_sections", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_job_details_org", "job_details", ["organization_id"])
    op.create_table(
        "hbl_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hbl_type", sa.String(6), nullable=False),
        sa.Column("last_no", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("organization_id", "hbl_type", name="uq_hbl_counter"),
    )


def downgrade():
    op.drop_table("hbl_counters")
    op.drop_table("job_details")
