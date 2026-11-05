"""Container operations: surveys, damages, repair orders, leases, EIR, stock counts (all additive)

Revision ID: 0057_container_operations
Revises: 0056_container_inventory
"""
import sqlalchemy as sa
from alembic import op

revision = "0057_container_operations"
down_revision = "0056_container_inventory"
branch_labels = None
depends_on = None

FK = sa.ForeignKey
NOW = sa.func.now()


def _org():
    return sa.Column("organization_id", sa.Integer(), FK("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


def _user(name):
    return sa.Column(name, sa.Integer(), FK("users.id", ondelete="SET NULL"))


def _cont(ondelete="RESTRICT"):
    return sa.Column("container_id", sa.Integer(), FK("containers.id", ondelete=ondelete), nullable=False, index=True)


def money(name, default=None):
    return sa.Column(name, sa.Numeric(14, 2), server_default=default)


def upgrade() -> None:
    op.create_table("container_surveys", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("number", sa.String(40)), _cont(),
                    sa.Column("survey_type", sa.String(120), nullable=False), sa.Column("survey_date", sa.Date(), nullable=False, index=True), sa.Column("depot", sa.String(120)),
                    sa.Column("surveyor", sa.String(120)), sa.Column("checklist", sa.JSON()), sa.Column("damage_location", sa.String(200)), sa.Column("damage_description", sa.String(2000)),
                    sa.Column("severity", sa.String(20)), sa.Column("recommended_repair", sa.String(1000)), money("repair_estimate"), sa.Column("remarks", sa.String(1000)),
                    sa.Column("status", sa.String(12), nullable=False, server_default="DRAFT", index=True), _user("decided_by"), sa.Column("decided_at", sa.DateTime(timezone=True)),
                    sa.Column("decision_note", sa.String(500)), _user("created_by"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
                    sa.UniqueConstraint("organization_id", "number", name="uq_container_survey_number"))
    op.create_table("container_damages", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("number", sa.String(40)), _cont(),
                    sa.Column("survey_id", sa.Integer(), FK("container_surveys.id", ondelete="SET NULL")), sa.Column("damage_date", sa.Date(), nullable=False),
                    sa.Column("damage_location", sa.String(200)), sa.Column("damage_type", sa.String(120)), sa.Column("severity", sa.String(20)), sa.Column("description", sa.String(2000)),
                    sa.Column("responsible_party", sa.String(160)), sa.Column("depot", sa.String(120)), money("estimated_cost"), money("actual_cost"),
                    sa.Column("claim_status", sa.String(12), nullable=False, server_default="NONE"), sa.Column("status", sa.String(12), nullable=False, server_default="OPEN", index=True),
                    _user("created_by"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW), sa.UniqueConstraint("organization_id", "number", name="uq_container_damage_number"))
    op.create_table("container_repair_orders", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("number", sa.String(40)), _cont(),
                    sa.Column("damage_id", sa.Integer(), FK("container_damages.id", ondelete="SET NULL")), sa.Column("category", sa.String(120)), sa.Column("description", sa.String(2000)),
                    sa.Column("vendor", sa.String(160)), sa.Column("workshop", sa.String(160)), money("labour", "0"), money("material", "0"), money("welding", "0"), money("painting", "0"),
                    money("cleaning", "0"), money("transport", "0"), money("other", "0"), sa.Column("currency", sa.String(3), nullable=False, server_default="INR"), money("estimated_total", "0"),
                    money("approved_amount"), money("actual_cost"), sa.Column("supplements", sa.JSON()), sa.Column("start_date", sa.Date()), sa.Column("completion_date", sa.Date()),
                    sa.Column("downtime_days", sa.Integer()), sa.Column("warranty_days", sa.Integer()), sa.Column("condition_after", sa.String(80)),
                    sa.Column("status", sa.String(12), nullable=False, server_default="ESTIMATED", index=True), sa.Column("status_note", sa.String(500)), _user("approved_by"), _user("qc_by"),
                    _user("created_by"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW), sa.UniqueConstraint("organization_id", "number", name="uq_container_repair_number"))
    op.create_table("container_leases", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("agreement_number", sa.String(60), nullable=False),
                    sa.Column("leasing_company", sa.String(160), nullable=False), sa.Column("start_date", sa.Date(), nullable=False), sa.Column("end_date", sa.Date()), money("daily_rental"), money("monthly_rental"),
                    sa.Column("currency", sa.String(3), nullable=False, server_default="USD"), sa.Column("free_days", sa.Integer(), nullable=False, server_default="0"), sa.Column("min_lease_days", sa.Integer()),
                    money("deposit"), sa.Column("redelivery_location", sa.String(160)), sa.Column("redelivery_conditions", sa.String(1000)), sa.Column("extensions", sa.JSON()),
                    sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE", index=True), _user("created_by"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
                    sa.UniqueConstraint("organization_id", "agreement_number", name="uq_container_lease_agreement"))
    op.create_table("container_lease_items", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("lease_id", sa.Integer(), FK("container_leases.id", ondelete="CASCADE"), nullable=False, index=True),
                    _cont(), sa.Column("on_hire_date", sa.Date(), nullable=False), sa.Column("off_hire_date", sa.Date()), money("daily_rate"),
                    sa.Column("status", sa.String(10), nullable=False, server_default="ON_HIRE", index=True), _user("created_by"), sa.UniqueConstraint("lease_id", "container_id", "on_hire_date", name="uq_lease_item"))
    op.create_table("container_eirs", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("number", sa.String(40)),
                    sa.Column("movement_id", sa.Integer(), FK("container_movements.id", ondelete="RESTRICT"), nullable=False), _cont(), sa.Column("gate_time", sa.DateTime(timezone=True)),
                    sa.Column("condition", sa.String(80)), sa.Column("damage_description", sa.String(1000)), sa.Column("driver_signed_by", sa.String(120)), sa.Column("party_signed_by", sa.String(120)),
                    sa.Column("remarks", sa.String(500)), _user("created_by"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
                    sa.UniqueConstraint("organization_id", "number", name="uq_container_eir_number"), sa.UniqueConstraint("movement_id", name="uq_container_eir_movement"))
    op.create_table("container_stock_counts", sa.Column("id", sa.Integer(), primary_key=True), _org(), sa.Column("number", sa.String(40)), sa.Column("depot", sa.String(120), nullable=False),
                    sa.Column("count_date", sa.Date(), nullable=False), sa.Column("found", sa.JSON()), sa.Column("missing", sa.JSON()), sa.Column("unexpected", sa.JSON()),
                    sa.Column("status", sa.String(10), nullable=False, server_default="OPEN"), _user("reviewed_by"), sa.Column("review_note", sa.String(500)), _user("created_by"),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW), sa.UniqueConstraint("organization_id", "number", name="uq_container_count_number"))


def downgrade() -> None:
    for t in ("container_stock_counts", "container_eirs", "container_lease_items", "container_leases", "container_repair_orders", "container_damages", "container_surveys"):
        op.drop_table(t)
