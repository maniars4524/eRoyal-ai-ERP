"""Output Docs: every generated report of a job, one row per version (never edited, never deleted)

Revision ID: 0062_job_output_docs
Revises: 0061_job_routing
"""
import sqlalchemy as sa
from alembic import op

revision = "0062_job_output_docs"
down_revision = "0061_job_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_output_docs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("variant", sa.String(20), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON()),
        sa.Column("generated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("shipment_id", "code", "variant", "version", name="uq_job_output_doc_version"),
    )


def downgrade() -> None:
    op.drop_table("job_output_docs")
