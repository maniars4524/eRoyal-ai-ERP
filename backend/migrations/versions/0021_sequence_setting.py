"""Sequence Setting: centrally configured numbering (configs, counters, issued-number registry, audit events).

Existing numbering keeps working: system-default sequences replicate today's formats lazily and continue from the
legacy doc_sequences counters, so nothing already issued is renumbered or reused.

Revision ID: 0021_sequence_setting
Revises: 0020_branch_details
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_sequence_setting"
down_revision = "0020_branch_details"
branch_labels = None
depends_on = None


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.create_table(
        "sequence_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("module", sa.String(40), nullable=False), sa.Column("doc_type", sa.String(40), nullable=False),
        sa.Column("branch_scope", sa.String(20), nullable=False, server_default="ALL"), sa.Column("fy_scope", sa.String(12), nullable=False, server_default="ALL"),
        sa.Column("shipment_scope", sa.String(40), nullable=False, server_default="ALL"),
        sa.Column("components", sa.JSON(), nullable=False), sa.Column("number_width", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("start_number", sa.BigInteger(), nullable=False, server_default="1"), sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reset_policy", sa.String(10), nullable=False, server_default="FY"), sa.Column("max_length", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("doc_code", sa.String(12)), sa.Column("fy_format", sa.String(10), nullable=False, server_default="YY-YY"), sa.Column("shipment_codes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="DRAFT"),
        sa.Column("effective_from", sa.Date()), sa.Column("effective_to", sa.Date()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("replaces_id", sa.Integer(), sa.ForeignKey("sequence_configs.id", ondelete="SET NULL")),
        sa.Column("is_system_default", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("remarks", sa.String(500)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts("created_at"), _ts("updated_at"),
    )
    op.create_index("ix_sequence_configs_org_doc", "sequence_configs", ["organization_id", "doc_type", "status"])

    op.create_table(
        "sequence_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("sequence_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_key", sa.String(12), nullable=False), sa.Column("last_number", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("config_id", "period_key", name="uq_sequence_counter"),
    )

    op.create_table(
        "issued_numbers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(40), nullable=False), sa.Column("number", sa.String(60), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("sequence_configs.id", ondelete="SET NULL")), sa.Column("config_version", sa.Integer()),
        sa.Column("period_key", sa.String(12)), sa.Column("branch_code", sa.String(20)), sa.Column("shipment_key", sa.String(40)),
        sa.Column("issued_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("related_type", sa.String(30)), sa.Column("related_id", sa.String(60)),
        _ts("issued_at"),
        sa.UniqueConstraint("organization_id", "doc_type", "number", name="uq_issued_number"),
    )
    op.create_index("ix_issued_numbers_config", "issued_numbers", ["config_id", "issued_at"])

    op.create_table(
        "sequence_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("sequence_configs.id", ondelete="SET NULL")),
        sa.Column("doc_type", sa.String(40)), sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous", sa.JSON()), sa.Column("new", sa.JSON()), sa.Column("reason", sa.Text()),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("actor_name", sa.String(160)),
        sa.Column("related_ref", sa.String(80)), sa.Column("result", sa.String(10), nullable=False, server_default="SUCCESS"),
        _ts("created_at"),
    )
    op.create_index("ix_sequence_events_config", "sequence_events", ["config_id", "created_at"])
    op.create_index("ix_sequence_events_org", "sequence_events", ["organization_id", "created_at"])
    op.execute(
        """
        CREATE FUNCTION sequence_events_immutable() RETURNS trigger AS $$
        BEGIN
          IF NEW.config_id IS NULL AND OLD.config_id IS NOT NULL AND (to_jsonb(NEW) - 'config_id') = (to_jsonb(OLD) - 'config_id') THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'sequence history records are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER sequence_events_immutable BEFORE UPDATE ON sequence_events FOR EACH ROW EXECUTE FUNCTION sequence_events_immutable()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS sequence_events_immutable ON sequence_events")
    op.execute("DROP FUNCTION IF EXISTS sequence_events_immutable()")
    for t in ("sequence_events", "issued_numbers", "sequence_counters", "sequence_configs"):
        op.drop_table(t)
