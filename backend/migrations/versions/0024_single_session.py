"""one active sign-in per user: session columns on users

Revision ID: 0024_single_session
Revises: 0023_company_logo
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_single_session"
down_revision = "0023_company_logo"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("session_id", sa.String(40)))
    op.add_column("users", sa.Column("session_ip", sa.String(60)))
    op.add_column("users", sa.Column("session_started_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("session_seen_at", sa.DateTime(timezone=True)))


def downgrade():
    for c in ("session_seen_at", "session_started_at", "session_ip", "session_id"):
        op.drop_column("users", c)
