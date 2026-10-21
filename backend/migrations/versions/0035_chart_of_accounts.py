"""chart of accounts: groups and ledgers

Revision ID: 0035_chart_of_accounts
Revises: 0034_line_job
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_chart_of_accounts"
down_revision = "0034_line_job"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "acc_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("acc_groups.id", ondelete="RESTRICT"), index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("code", sa.String(12), nullable=False),
        sa.Column("nature", sa.String(20), nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "parent_id", "name", name="uq_accgroup_org_parent_name"),
    )
    op.create_table(
        "acc_ledgers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("group_id", sa.Integer, sa.ForeignKey("acc_groups.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("alias", sa.String(120)),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("opening_balance", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("opening_side", sa.String(2), nullable=False, server_default="Dr"),
        sa.Column("control", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("system_key", sa.String(30)),
        sa.Column("source_type", sa.String(20)),
        sa.Column("source_id", sa.Integer),
        sa.Column("source_role", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "code", name="uq_accledger_org_code"),
    )
    op.create_index("ix_acc_ledgers_source", "acc_ledgers", ["organization_id", "source_type", "source_id"])


def downgrade():
    op.drop_table("acc_ledgers")
    op.drop_table("acc_groups")
