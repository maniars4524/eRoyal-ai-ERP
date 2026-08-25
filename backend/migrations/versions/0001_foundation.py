"""Create authentication and tenant foundation.

Revision ID: 0001_foundation
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("organizations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(20), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("legal_name", sa.String(200)), sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"), sa.Column("country", sa.String(80), nullable=False, server_default="India"), sa.Column("created_by", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.UniqueConstraint("code"))
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE")), sa.Column("email", sa.String(254), nullable=False), sa.Column("username", sa.String(80)), sa.Column("password_hash", sa.String(512), nullable=False), sa.Column("first_name", sa.String(80), nullable=False), sa.Column("last_name", sa.String(80), nullable=False, server_default=""), sa.Column("phone", sa.String(40)), sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"), sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("last_login_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.UniqueConstraint("email"), sa.UniqueConstraint("username"))
    op.create_foreign_key("fk_organizations_created_by", "organizations", "users", ["created_by"], ["id"])
    op.create_table("permissions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(100), nullable=False, unique=True), sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.String(240)), sa.Column("module", sa.String(60), nullable=False), sa.Column("action", sa.String(40), nullable=False))
    op.create_table("roles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE")), sa.Column("name", sa.String(60), nullable=False), sa.Column("description", sa.String(240)), sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"), sa.UniqueConstraint("organization_id", "name", name="uq_role_org_name"))
    op.create_table("user_roles", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("role_permissions", sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True), sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("audit_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="SET NULL")), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("action", sa.String(80), nullable=False), sa.Column("module", sa.String(60), nullable=False), sa.Column("entity_type", sa.String(80)), sa.Column("entity_id", sa.String(80)), sa.Column("old_value", sa.JSON()), sa.Column("new_value", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("shipments", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False), sa.Column("job_number", sa.String(40), nullable=False, unique=True), sa.Column("mode", sa.String(20), nullable=False), sa.Column("direction", sa.String(20), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="Draft"), sa.Column("revenue", sa.Numeric(14, 2), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))


def downgrade():
    op.drop_table("shipments")
    op.drop_table("audit_logs")
    op.drop_table("role_permissions")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_constraint("fk_organizations_created_by", "organizations", type_="foreignkey")
    op.drop_table("users")
    op.drop_table("organizations")
