"""network masters: airlines, shipping lines, IATA, CFS and airline stock lines, all linked to the Organization Master

Revision ID: 0042_network_masters
Revises: 0041_fix_document_party
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_network_masters"
down_revision = "0041_fix_document_party"
branch_labels = None
depends_on = None


def _base(name, *extra, unique):
    return (name, [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="SET NULL"), index=True),
        *extra,
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE", index=True),
        sa.Column("role_off", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", unique, name="uq_" + name),
    ])


def upgrade():
    S = sa.String
    n, c = _base("airlines",
                 sa.Column("code", S(10), nullable=False), sa.Column("name", S(200), nullable=False), sa.Column("sub_name", S(200)), sa.Column("num_code", S(10)), sa.Column("icao_code", S(10)), sa.Column("ac_code", S(20)),
                 sa.Column("myc_commission", sa.Numeric(7, 2), nullable=False, server_default="0"), sa.Column("country", S(80)), sa.Column("address1", S(200)), sa.Column("address2", S(200)), sa.Column("city", S(80)),
                 sa.Column("state", S(80)), sa.Column("email", S(254)), sa.Column("phone", S(40)), sa.Column("source_id", sa.Integer), unique="code")
    op.create_table(n, *c)
    n, c = _base("shipping_lines",
                 sa.Column("code", S(20), nullable=False), sa.Column("name", S(200), nullable=False), sa.Column("sub_name", S(200)), sa.Column("scac_code", S(10)), sa.Column("country", S(80)), sa.Column("address1", S(200)),
                 sa.Column("address2", S(200)), sa.Column("city", S(80)), sa.Column("state", S(80)), sa.Column("email", S(254)), sa.Column("phone", S(40)), sa.Column("website", S(200)), unique="code")
    op.create_table(n, *c)
    n, c = _base("iata_records",
                 sa.Column("airline_id", sa.Integer, sa.ForeignKey("airlines.id", ondelete="SET NULL"), index=True), sa.Column("iata_code", S(20), nullable=False), sa.Column("name", S(200), nullable=False), sa.Column("cass_code", S(20)),
                 sa.Column("country", S(80)), sa.Column("address", S(300)), sa.Column("city", S(80)), sa.Column("contact_person", S(120)), sa.Column("phone", S(40)), sa.Column("email", S(254)), unique="iata_code")
    op.create_table(n, *c)
    n, c = _base("cfs_records",
                 sa.Column("code", S(20), nullable=False), sa.Column("name", S(200), nullable=False), sa.Column("location", S(200)), sa.Column("city", S(80)), sa.Column("state", S(80)), sa.Column("country", S(80)),
                 sa.Column("port_code", S(10)), sa.Column("contact_person", S(120)), sa.Column("phone", S(40)), sa.Column("email", S(254)), unique="code")
    op.create_table(n, *c)
    op.create_table(
        "airline_stock_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("airline_id", sa.Integer, sa.ForeignKey("airlines.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="SET NULL"), index=True),
        sa.Column("bill_type", S(10), nullable=False, server_default="MASTER"),
        sa.Column("from_awb", S(12), nullable=False), sa.Column("to_awb", S(12), nullable=False), sa.Column("shipper", S(200)),
        sa.Column("myc_value", sa.Numeric(14, 2), nullable=False, server_default="0"), sa.Column("cancel_charge", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("received_date", sa.Date), sa.Column("ref_no", S(40)), sa.Column("remarks", S(400)),
        sa.Column("status", S(20), nullable=False, server_default="ACTIVE", index=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    for t in ("airline_stock_lines", "cfs_records", "iata_records", "shipping_lines", "airlines"):
        op.drop_table(t)
