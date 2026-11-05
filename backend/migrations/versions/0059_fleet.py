"""Transport fleet: vehicles, history, documents, work orders, maintenance plans, tyres, tyre events, fuel (all additive)

Revision ID: 0059_fleet
Revises: 0058_container_files
"""
import sqlalchemy as sa
from alembic import op

revision = "0059_fleet"
down_revision = "0058_container_files"
branch_labels = None
depends_on = None
FK = sa.ForeignKey
NOW = sa.func.now()


def org():
    return sa.Column("organization_id", sa.Integer(), FK("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


def usr(name):
    return sa.Column(name, sa.Integer(), FK("users.id", ondelete="SET NULL"))


def veh(ondelete="CASCADE", nullable=False):
    return sa.Column("vehicle_id", sa.Integer(), FK("vehicles.id", ondelete=ondelete), nullable=nullable, index=True)


def money(name, default=None):
    return sa.Column(name, sa.Numeric(14, 2), server_default=default)


def ts():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW)


def S(name, n, **kw):
    return sa.Column(name, sa.String(n), **kw)


def upgrade() -> None:
    op.create_table("vehicles", sa.Column("id", sa.Integer(), primary_key=True), org(), S("asset_id", 40), S("registration_number", 20, nullable=False), S("category", 80, index=True), S("vehicle_type", 80), S("make", 80), S("model", 80),
                    sa.Column("manufacturing_year", sa.Integer()), S("hand", 20), S("chassis_number", 40, index=True), S("engine_number", 40), S("vin", 40), S("fuel_type", 20), S("engine_capacity", 20),
                    sa.Column("tonnage", sa.Numeric(10, 2)), sa.Column("payload_capacity", sa.Numeric(10, 2)), S("axle_config", 80), sa.Column("purchase_date", sa.Date()), money("purchase_cost"), S("supplier", 160), S("purchase_invoice", 60),
                    sa.Column("warranty_until", sa.Date()), sa.Column("odometer", sa.Integer(), nullable=False, server_default="0"), S("location", 120), S("driver", 120), S("branch", 80, index=True), S("ownership", 80, index=True),
                    sa.Column("ownership_data", sa.JSON()), S("status", 20, nullable=False, server_default="AVAILABLE", index=True), S("remarks", 2000), sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
                    usr("created_by"), ts(), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW),
                    sa.UniqueConstraint("organization_id", "registration_number", name="uq_vehicle_registration"), sa.UniqueConstraint("organization_id", "asset_id", name="uq_vehicle_asset_id"))
    op.create_table("vehicle_history", sa.Column("id", sa.Integer(), primary_key=True), org(), veh(), S("field", 30, nullable=False), S("old_value", 200), S("new_value", 200), S("reason", 500), usr("user_id"), ts())
    op.create_table("vehicle_documents", sa.Column("id", sa.Integer(), primary_key=True), org(), veh(), S("doc_type", 120, nullable=False), S("number", 80), sa.Column("issue_date", sa.Date()),
                    sa.Column("expiry_date", sa.Date(), index=True), S("authority", 160), sa.Column("mandatory", sa.Boolean(), nullable=False, server_default="false"), S("status", 12, nullable=False, server_default="CURRENT", index=True),
                    S("remarks", 300), usr("created_by"), ts())
    op.create_table("vehicle_work_orders", sa.Column("id", sa.Integer(), primary_key=True), org(), S("number", 40), veh("RESTRICT"), sa.Column("plan_id", sa.Integer(), index=True), S("category", 120), S("complaint", 1000),
                    S("reported_by", 120), sa.Column("odometer", sa.Integer()), sa.Column("engine_hours", sa.Numeric(10, 1)), S("workshop", 160), S("mechanic", 120), S("inspection_result", 1000), S("repair_description", 2000),
                    S("parts_note", 1000), sa.Column("labour_hours", sa.Numeric(8, 1)), money("labour_cost", "0"), money("material_cost", "0"), money("external_cost", "0"), money("other_cost", "0"), money("total_cost", "0"),
                    sa.Column("start_date", sa.Date()), sa.Column("completion_date", sa.Date()), sa.Column("downtime_days", sa.Integer()), sa.Column("next_service_date", sa.Date()), sa.Column("next_service_odometer", sa.Integer()),
                    S("status", 12, nullable=False, server_default="OPEN", index=True), S("status_note", 500), usr("approved_by"), usr("created_by"), ts(), sa.UniqueConstraint("organization_id", "number", name="uq_vehicle_wo_number"))
    op.create_table("vehicle_plans", sa.Column("id", sa.Integer(), primary_key=True), org(), veh(), S("name", 120, nullable=False), S("category", 120), sa.Column("interval_km", sa.Integer()), sa.Column("interval_days", sa.Integer()),
                    sa.Column("last_done_odometer", sa.Integer()), sa.Column("last_done_date", sa.Date()), sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"), usr("created_by"), ts())
    op.create_table("tyres", sa.Column("id", sa.Integer(), primary_key=True), org(), S("serial_number", 60, nullable=False), S("brand", 80, index=True), S("category", 80), S("model", 80), S("size", 40),
                    S("condition_type", 10, nullable=False, server_default="NEW"), sa.Column("purchase_date", sa.Date()), money("purchase_cost"), S("supplier", 160), sa.Column("warranty_until", sa.Date()),
                    sa.Column("tread_depth_mm", sa.Numeric(5, 1)), S("status", 12, nullable=False, server_default="IN_STOCK", index=True), S("warehouse", 120), sa.Column("current_vehicle_id", sa.Integer(), FK("vehicles.id", ondelete="SET NULL"), index=True),
                    S("current_position", 60), sa.Column("fitted_odometer", sa.Integer()), sa.Column("retread_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("total_km", sa.Integer(), nullable=False, server_default="0"),
                    money("extra_cost", "0"), S("scrap_reason", 300), S("remarks", 500), usr("created_by"), ts(), sa.UniqueConstraint("organization_id", "serial_number", name="uq_tyre_serial"))
    op.create_table("tyre_events", sa.Column("id", sa.Integer(), primary_key=True), org(), sa.Column("tyre_id", sa.Integer(), FK("tyres.id", ondelete="CASCADE"), nullable=False, index=True),
                    sa.Column("vehicle_id", sa.Integer(), FK("vehicles.id", ondelete="SET NULL"), index=True), S("event", 14, nullable=False), S("position", 60), S("from_position", 60), sa.Column("odometer", sa.Integer()),
                    sa.Column("tread_depth_mm", sa.Numeric(5, 1)), money("cost"), S("reason", 300), usr("user_id"), ts())
    op.create_table("fuel_transactions", sa.Column("id", sa.Integer(), primary_key=True), org(), veh("RESTRICT"), S("kind", 10, nullable=False, server_default="FUEL"), sa.Column("txn_date", sa.Date(), nullable=False, index=True),
                    sa.Column("odometer", sa.Integer()), sa.Column("litres", sa.Numeric(10, 2), nullable=False), money("amount"), S("fuel_station", 120), S("fuel_card", 60), S("driver", 120),
                    sa.Column("full_tank", sa.Boolean(), nullable=False, server_default="true"), sa.Column("km_per_litre", sa.Numeric(8, 2)), sa.Column("abnormal", sa.Boolean(), nullable=False, server_default="false"), usr("created_by"), ts())


def downgrade() -> None:
    for t in ("fuel_transactions", "tyre_events", "tyres", "vehicle_plans", "vehicle_work_orders", "vehicle_documents", "vehicle_history", "vehicles"):
        op.drop_table(t)
