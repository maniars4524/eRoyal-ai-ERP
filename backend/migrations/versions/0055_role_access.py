"""Role-based access foundation: company branches, user placement, customer assignments, audit branch (all additive)

Revision ID: 0055_role_access
Revises: 0054_breakup_and_pct_base
"""
import sqlalchemy as sa
from alembic import op

revision = "0055_role_access"
down_revision = "0054_breakup_and_pct_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_branches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(60), index=True),
        sa.Column("city", sa.String(80)),
        sa.Column("state", sa.String(80)),
        sa.Column("is_head_office", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "code", name="uq_company_branch_code"),
    )
    op.create_table(
        "user_access",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("company_branches.id", ondelete="SET NULL"), index=True),
        sa.Column("extra_branch_ids", sa.JSON()),
        sa.Column("region", sa.String(60)),
        sa.Column("user_category", sa.String(30), index=True),
        sa.Column("designation", sa.String(80)),
        sa.Column("reporting_manager_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), index=True),
        sa.Column("business_vertical", sa.String(40)),
        sa.Column("job_categories", sa.JSON()),
        sa.Column("salesperson_name", sa.String(120)),
        sa.Column("dashboards", sa.JSON()),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "customer_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("assigned_by", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("customer_id", "user_id", name="uq_customer_assignment"),
    )
    op.add_column("audit_logs", sa.Column("branch_id", sa.Integer(), nullable=True))
    op.create_index("ix_audit_logs_branch_id", "audit_logs", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_branch_id", table_name="audit_logs")
    op.drop_column("audit_logs", "branch_id")
    op.drop_table("customer_assignments")
    op.drop_table("user_access")
    op.drop_table("company_branches")
