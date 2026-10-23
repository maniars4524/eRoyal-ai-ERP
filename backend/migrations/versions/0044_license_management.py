"""license management: license terms on organizations, renewals, expiry notifications

Revision ID: 0044_license_management
Revises: 0043_job_branch_salesperson
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_license_management"
down_revision = "0043_job_branch_salesperson"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # additive and nullable / defaulted: existing companies keep behaving exactly as before (no expiry, no limits)
    op.add_column("organizations", sa.Column("license_start", sa.Date(), nullable=True))
    op.add_column("organizations", sa.Column("license_expiry", sa.Date(), nullable=True))
    op.add_column("organizations", sa.Column("renewal_cycle", sa.String(10), nullable=True))
    op.add_column("organizations", sa.Column("max_users", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("max_sessions", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("modules", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("contact", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("license_notes", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("demo_portal", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_organizations_license_expiry", "organizations", ["license_expiry"])
    op.create_table(
        "license_renewals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("cycle", sa.String(10), nullable=False),
        sa.Column("previous_expiry", sa.Date(), nullable=True),
        sa.Column("new_expiry", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("performed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "request_id", name="uq_license_renewal_request"),
    )
    op.create_table(
        "license_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("channel_status", sa.String(20), nullable=False, server_default="IN_APP"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "kind", "expiry_date", name="uq_license_notice"),
    )


def downgrade() -> None:
    op.drop_table("license_notifications")
    op.drop_table("license_renewals")
    op.drop_index("ix_organizations_license_expiry", table_name="organizations")
    for c in ("demo_portal", "license_notes", "contact", "modules", "max_sessions", "max_users", "renewal_cycle", "license_expiry", "license_start"):
        op.drop_column("organizations", c)
