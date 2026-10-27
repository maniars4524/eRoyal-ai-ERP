"""HBL Clause Master (Masters > Sea Export > HBL Clause Master)

Revision ID: 0047_hbl_clauses
Revises: 0046_job_charge_breakup
"""
import sqlalchemy as sa
from alembic import op

revision = "0047_hbl_clauses"
down_revision = "0046_job_charge_breakup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hbl_clauses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="Other"),
        sa.Column("shipment_type", sa.String(4), nullable=False, server_default="Both"),
        sa.Column("movement_type", sa.String(20), nullable=False, server_default="All"),
        sa.Column("country", sa.String(80)),
        sa.Column("pol", sa.String(80)),
        sa.Column("pod", sa.String(80)),
        sa.Column("freight_term", sa.String(8), nullable=False, server_default="Both"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("exclusive_group", sa.String(40)),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE", index=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "code", name="uq_hbl_clause_code"),
    )


def downgrade() -> None:
    op.drop_table("hbl_clauses")
