"""Container photos / documents and the full EIR form details (additive)

Revision ID: 0058_container_files
Revises: 0057_container_operations
"""
import sqlalchemy as sa
from alembic import op

revision = "0058_container_files"
down_revision = "0057_container_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("container_eirs", sa.Column("details", sa.JSON()))
    op.create_table(
        "container_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entity_type", sa.String(20), nullable=False, index=True),
        sa.Column("entity_id", sa.Integer(), nullable=False, index=True),
        sa.Column("category", sa.String(12), nullable=False, server_default="PHOTO"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("caption", sa.String(300)),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("container_attachments")
    op.drop_column("container_eirs", "details")
