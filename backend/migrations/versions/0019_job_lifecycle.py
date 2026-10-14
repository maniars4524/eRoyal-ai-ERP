"""Job stage, manual closure / reopening and the permanent job activity log.

Existing jobs keep their numbers and data: they start at stage "Job Created" with closure status OPEN, and one
JOB_CREATED event is back-filled from the reliable created_at / created_by columns (nothing else is invented).
History tables cannot be updated (trigger); deleting a job keeps its history (job_id becomes NULL, job_number stays).

Revision ID: 0019_job_lifecycle
Revises: 0018_org_branches_credit
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_job_lifecycle"
down_revision = "0018_org_branches_credit"
branch_labels = None
depends_on = None


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.add_column("shipments", sa.Column("job_stage", sa.String(40), nullable=False, server_default="Job Created"))
    op.add_column("shipments", sa.Column("stage_source", sa.String(10), nullable=False, server_default="AUTO"))
    op.add_column("shipments", sa.Column("stage_reason", sa.Text()))
    op.add_column("shipments", sa.Column("stage_updated_at", sa.DateTime(timezone=True)))
    op.add_column("shipments", sa.Column("closure_status", sa.String(10), nullable=False, server_default="OPEN"))
    op.add_column("shipments", sa.Column("closure_cycle", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("shipments", sa.Column("last_closed_at", sa.DateTime(timezone=True)))
    op.add_column("shipments", sa.Column("last_closed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("shipments", sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("shipments", sa.Column("last_activity_at", sa.DateTime(timezone=True)))
    op.create_index("ix_shipments_closure_status", "shipments", ["closure_status"])
    op.create_index("ix_shipments_job_stage", "shipments", ["job_stage"])
    op.execute("UPDATE shipments SET stage_updated_at = created_at, last_activity_at = created_at")

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="SET NULL")),
        sa.Column("job_number", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("previous_value", sa.Text()), sa.Column("new_value", sa.Text()),
        sa.Column("actor_type", sa.String(12), nullable=False, server_default="USER"),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_name", sa.String(160)),
        sa.Column("source", sa.String(20), nullable=False, server_default="API"),
        sa.Column("reason", sa.Text()),
        sa.Column("related_type", sa.String(30)), sa.Column("related_id", sa.String(60)),
        sa.Column("correlation_id", sa.String(60)), sa.Column("approval_ref", sa.String(60)),
        sa.Column("result", sa.String(10), nullable=False, server_default="SUCCESS"),
        sa.Column("idempotency_key", sa.String(80)),
        sa.Column("detail", sa.JSON(), nullable=False),
        _ts(),
    )
    op.create_index("ix_job_events_org_job", "job_events", ["organization_id", "shipment_id", "created_at"])
    op.create_index("ix_job_events_job_number", "job_events", ["organization_id", "job_number", "created_at"])
    op.create_index("ix_job_events_type", "job_events", ["event_type"])
    op.create_index("ix_job_events_category", "job_events", ["category"])
    op.create_index("ix_job_events_actor", "job_events", ["actor_id"])
    op.create_index("ix_job_events_related", "job_events", ["related_type", "related_id"])
    op.create_index("ix_job_events_correlation", "job_events", ["correlation_id"])
    op.create_index("uq_job_events_idem", "job_events", ["organization_id", "idempotency_key"], unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"))

    op.create_table(
        "job_stage_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="SET NULL")),
        sa.Column("job_number", sa.String(40), nullable=False),
        sa.Column("previous_stage", sa.String(40)), sa.Column("new_stage", sa.String(40), nullable=False),
        sa.Column("source", sa.String(10), nullable=False), sa.Column("trigger", sa.String(200)), sa.Column("reason", sa.Text()),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("actor_name", sa.String(160)),
        sa.Column("related_ref", sa.String(80)), sa.Column("approval_ref", sa.String(60)), sa.Column("event_id", sa.Integer()),
        _ts(),
    )
    op.create_index("ix_job_stage_history_job", "job_stage_history", ["organization_id", "shipment_id", "created_at"])

    op.create_table(
        "job_closures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="SET NULL")),
        sa.Column("job_number", sa.String(40), nullable=False),
        sa.Column("cycle_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("previous_status", sa.String(10), nullable=False), sa.Column("new_status", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("remarks", sa.Text()),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("actor_name", sa.String(160)),
        sa.Column("approval_ref", sa.String(60)), sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("final_buy", sa.Numeric(16, 2)), sa.Column("final_sell", sa.Numeric(16, 2)), sa.Column("final_margin", sa.Numeric(16, 2)),
        sa.Column("previous_closure_id", sa.Integer()), sa.Column("event_id", sa.Integer()),
        _ts(),
    )
    op.create_index("ix_job_closures_job", "job_closures", ["organization_id", "shipment_id", "created_at"])
    op.create_index("uq_job_closure_cycle", "job_closures", ["shipment_id", "cycle_no", "action"], unique=True, postgresql_where=sa.text("shipment_id IS NOT NULL"))

    op.create_table(
        "job_milestones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remarks", sa.Text()),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("shipment_id", "code", name="uq_job_milestone"),
    )
    op.create_index("ix_job_milestones_org", "job_milestones", ["organization_id"])

    # reliable back-fill: one JOB_CREATED event per existing job
    op.execute(
        "INSERT INTO job_events (organization_id, shipment_id, job_number, event_type, category, description, actor_type, actor_id, source, result, detail, created_at) "
        "SELECT organization_id, id, job_number, 'JOB_CREATED', 'JOB', 'Job created (back-filled from the job record)', CASE WHEN created_by IS NULL THEN 'SYSTEM' ELSE 'USER' END, created_by, 'BACKFILL', 'SUCCESS', '{}'::json, created_at FROM shipments")

    # history is append-only: block UPDATEs (a job being deleted may only null out its job reference)
    op.execute(
        """
        CREATE FUNCTION job_history_immutable() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF NEW.shipment_id IS NULL AND OLD.shipment_id IS NOT NULL AND (to_jsonb(NEW) - 'shipment_id') = (to_jsonb(OLD) - 'shipment_id') THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'job history records are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for t in ("job_events", "job_stage_history", "job_closures"):
        op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION job_history_immutable()")


def downgrade():
    for t in ("job_events", "job_stage_history", "job_closures"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
    op.execute("DROP FUNCTION IF EXISTS job_history_immutable()")
    for t in ("job_milestones", "job_closures", "job_stage_history", "job_events"):
        op.drop_table(t)
    for c in ("last_activity_at", "reopen_count", "last_closed_by", "last_closed_at", "closure_cycle", "closure_status", "stage_updated_at", "stage_reason", "stage_source", "job_stage"):
        op.drop_column("shipments", c)
