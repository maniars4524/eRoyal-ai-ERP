"""Jobs are cancelled with a reason, never deleted: cancel columns, and a database guard that refuses DELETE on shipments

Revision ID: 0060_job_cancel_protect
Revises: 0059_fleet
"""
import sqlalchemy as sa
from alembic import op

revision = "0060_job_cancel_protect"
down_revision = "0059_fleet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("cancel_reason", sa.String(500)))
    op.add_column("shipments", sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("shipments", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.execute("""
    CREATE OR REPLACE FUNCTION esway_block_job_delete() RETURNS trigger AS $$
    BEGIN
        IF current_setting('esway.allow_job_delete', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Jobs are never deleted: cancel job % with a reason instead', OLD.job_number USING ERRCODE = 'restrict_violation';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER esway_block_job_delete BEFORE DELETE ON shipments FOR EACH ROW EXECUTE FUNCTION esway_block_job_delete();")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS esway_block_job_delete ON shipments;")
    op.execute("DROP FUNCTION IF EXISTS esway_block_job_delete();")
    op.drop_column("shipments", "cancelled_at")
    op.drop_column("shipments", "cancelled_by")
    op.drop_column("shipments", "cancel_reason")
