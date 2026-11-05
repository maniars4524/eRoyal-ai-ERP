"""Container inventory: containers, status history, movements (all additive)

Revision ID: 0056_container_inventory
Revises: 0055_role_access
"""
import sqlalchemy as sa
from alembic import op

revision = "0056_container_inventory"
down_revision = "0055_role_access"
branch_labels = None
depends_on = None


def _fk(name, ondelete):
    return sa.ForeignKey(name, ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "containers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), _fk("organizations.id", "CASCADE"), nullable=False, index=True),
        sa.Column("container_number", sa.String(11), nullable=False),
        sa.Column("size", sa.String(60), index=True),
        sa.Column("container_type", sa.String(80), index=True),
        sa.Column("iso_code", sa.String(10)),
        sa.Column("manufacturer", sa.String(120)),
        sa.Column("manufacturing_year", sa.Integer()),
        sa.Column("manufacturing_country", sa.String(80)),
        sa.Column("purchase_date", sa.Date()),
        sa.Column("hand", sa.String(20)),
        sa.Column("serial_number", sa.String(60)),
        sa.Column("csc_plate", sa.String(120)),
        sa.Column("csc_valid_until", sa.Date()),
        sa.Column("description", sa.String(2000)),
        sa.Column("remarks", sa.String(2000)),
        sa.Column("ownership", sa.String(80), index=True),
        sa.Column("ownership_data", sa.JSON()),
        sa.Column("grade", sa.String(40), index=True),
        sa.Column("condition", sa.String(80), index=True),
        sa.Column("availability", sa.String(20), nullable=False, server_default="NOT_AVAILABLE", index=True),
        sa.Column("location_status", sa.String(20), nullable=False, server_default="NOT_AT_DEPOT", index=True),
        sa.Column("lease_status", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("depot", sa.String(120), index=True),
        sa.Column("depot_code", sa.String(40)),
        sa.Column("yard_location", sa.String(60)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("data", sa.JSON()),
        sa.Column("created_by", sa.Integer(), _fk("users.id", "SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "container_number", name="uq_container_number"),
    )
    op.create_table(
        "container_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), _fk("organizations.id", "CASCADE"), nullable=False, index=True),
        sa.Column("container_id", sa.Integer(), _fk("containers.id", "CASCADE"), nullable=False, index=True),
        sa.Column("field", sa.String(30), nullable=False),
        sa.Column("old_value", sa.String(200)),
        sa.Column("new_value", sa.String(200)),
        sa.Column("reason", sa.String(500)),
        sa.Column("movement_id", sa.Integer()),
        sa.Column("user_id", sa.Integer(), _fk("users.id", "SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "container_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), _fk("organizations.id", "CASCADE"), nullable=False, index=True),
        sa.Column("number", sa.String(40)),
        sa.Column("container_id", sa.Integer(), _fk("containers.id", "RESTRICT"), nullable=False, index=True),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("movement_type", sa.String(120)),
        sa.Column("from_depot", sa.String(120)),
        sa.Column("to_depot", sa.String(120)),
        sa.Column("from_location", sa.String(60)),
        sa.Column("to_location", sa.String(60)),
        sa.Column("movement_date", sa.Date(), nullable=False, index=True),
        sa.Column("movement_time", sa.String(8)),
        sa.Column("truck_number", sa.String(30)),
        sa.Column("driver_name", sa.String(80)),
        sa.Column("transport_company", sa.String(120)),
        sa.Column("customer", sa.String(160)),
        sa.Column("agent", sa.String(160)),
        sa.Column("booking_number", sa.String(40)),
        sa.Column("bl_number", sa.String(40)),
        sa.Column("job_reference", sa.String(40)),
        sa.Column("eir_number", sa.String(40)),
        sa.Column("reason", sa.String(500)),
        sa.Column("condition_departure", sa.String(80)),
        sa.Column("condition_arrival", sa.String(80)),
        sa.Column("damage_remarks", sa.String(1000)),
        sa.Column("status", sa.String(12), nullable=False, server_default="DRAFT", index=True),
        sa.Column("before_state", sa.JSON()),
        sa.Column("created_by", sa.Integer(), _fk("users.id", "SET NULL")),
        sa.Column("approved_by", sa.Integer(), _fk("users.id", "SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("reversed_by", sa.Integer(), _fk("users.id", "SET NULL")),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column("reversal_reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "number", name="uq_container_movement_number"),
    )


def downgrade() -> None:
    op.drop_table("container_movements")
    op.drop_table("container_status_history")
    op.drop_table("containers")
