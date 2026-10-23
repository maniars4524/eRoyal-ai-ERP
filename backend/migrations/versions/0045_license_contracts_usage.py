"""license management: AMC / MMC maintenance contracts, renewal payments, per-user daily usage

Revision ID: 0045_license_contracts_usage
Revises: 0044_license_management
"""
import sqlalchemy as sa
from alembic import op

revision = "0045_license_contracts_usage"
down_revision = "0044_license_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("maintenance_plan", sa.String(10), nullable=True))
    op.add_column("organizations", sa.Column("maintenance_expiry", sa.Date(), nullable=True))
    op.add_column("license_renewals", sa.Column("kind", sa.String(12), nullable=False, server_default="LICENSE"))
    op.add_column("license_renewals", sa.Column("amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("license_renewals", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column("license_renewals", sa.Column("payment_method", sa.String(20), nullable=True))
    op.add_column("license_renewals", sa.Column("payment_status", sa.String(10), nullable=False, server_default="PENDING"))
    op.add_column("license_renewals", sa.Column("payment_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "user_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_user_usage_day"),
    )


def downgrade() -> None:
    op.drop_table("user_usage")
    for c in ("payment_updated_at", "payment_status", "payment_method", "currency", "amount", "kind"):
        op.drop_column("license_renewals", c)
    op.drop_column("organizations", "maintenance_expiry")
    op.drop_column("organizations", "maintenance_plan")
