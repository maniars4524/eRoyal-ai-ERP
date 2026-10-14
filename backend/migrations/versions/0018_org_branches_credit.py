"""Organization Master enhancement: branches, branch contacts, credit facilities, credit requests, credit audit events.

Existing organizations (customers) are untouched; shipments gain an optional party_branch_id.

Revision ID: 0018_org_branches_credit
Revises: 0017_crm_records
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_org_branches_credit"
down_revision = "0017_crm_records"
branch_labels = None
depends_on = None
M = sa.Numeric(16, 2)


def _fk(col, target, ondelete, nullable=True):
    return sa.Column(col, sa.Integer(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.create_table(
        "org_branches",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("organization_id", "organizations.id", "CASCADE", False), _fk("customer_id", "customers.id", "CASCADE", False),
        sa.Column("code", sa.String(20), nullable=False), sa.Column("name", sa.String(160), nullable=False),
        sa.Column("state", sa.String(80)), sa.Column("country", sa.String(80)), sa.Column("city", sa.String(80)), sa.Column("address", sa.String(400)),
        sa.Column("pincode", sa.String(12)), sa.Column("phone", sa.String(40)), sa.Column("email", sa.String(254)), sa.Column("gstin", sa.String(30)),
        sa.Column("billing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        _fk("created_by", "users.id", "SET NULL"), _ts("created_at"), _ts("updated_at"),
        sa.UniqueConstraint("customer_id", "code", name="uq_org_branch_code"),
    )
    op.create_index("ix_org_branches_organization_id", "org_branches", ["organization_id"])
    op.create_index("ix_org_branches_customer_id", "org_branches", ["customer_id"])

    op.create_table(
        "org_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("organization_id", "organizations.id", "CASCADE", False), _fk("customer_id", "customers.id", "CASCADE", False), _fk("branch_id", "org_branches.id", "SET NULL"),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("designation", sa.String(80)), sa.Column("department", sa.String(40)),
        sa.Column("mobile", sa.String(40)), sa.Column("whatsapp", sa.String(40)), sa.Column("email", sa.String(254)), sa.Column("landline", sa.String(40)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("is_billing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_operational", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        _ts("created_at"), _ts("updated_at"),
    )
    for c in ("organization_id", "customer_id", "branch_id"):
        op.create_index(f"ix_org_contacts_{c}", "org_contacts", [c])

    op.create_table(
        "credit_facilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("organization_id", "organizations.id", "CASCADE", False), _fk("customer_id", "customers.id", "CASCADE", False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="NEW"), sa.Column("status", sa.String(14), nullable=False, server_default="DRAFT"),
        sa.Column("credit_days", sa.Integer(), nullable=False, server_default="0"), sa.Column("requested_limit", M, nullable=False, server_default="0"), sa.Column("approved_limit", M),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"), sa.Column("agreement_no", sa.String(80)), sa.Column("agreement_file", sa.String(240)),
        sa.Column("policy", sa.String(6), nullable=False, server_default="SOFT"), sa.Column("branch_limits", sa.JSON(), nullable=False), sa.Column("reason", sa.Text()),
        sa.Column("prev_id", sa.Integer(), sa.ForeignKey("credit_facilities.id", ondelete="SET NULL")),
        _fk("requested_by", "users.id", "SET NULL"), _ts("requested_at"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)), _fk("reviewed_by", "users.id", "SET NULL"), _fk("approved_by", "users.id", "SET NULL"),
        sa.Column("approved_at", sa.DateTime(timezone=True)), sa.Column("start_date", sa.Date()), sa.Column("expiry_date", sa.Date()), sa.Column("approval_remarks", sa.Text()),
    )
    op.create_index("ix_credit_facilities_organization_id", "credit_facilities", ["organization_id"])
    op.create_index("ix_credit_facilities_customer_id", "credit_facilities", ["customer_id"])
    op.create_index("ix_credit_facilities_status", "credit_facilities", ["status"])

    op.create_table(
        "credit_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("organization_id", "organizations.id", "CASCADE", False), _fk("customer_id", "customers.id", "CASCADE", False),
        _fk("facility_id", "credit_facilities.id", "SET NULL"), _fk("branch_id", "org_branches.id", "SET NULL"),
        sa.Column("kind", sa.String(10), nullable=False), sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("days", sa.Integer()), sa.Column("amount", M), sa.Column("one_time", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""), _fk("requested_by", "users.id", "SET NULL"), _ts("requested_at"),
        _fk("decided_by", "users.id", "SET NULL"), sa.Column("decided_at", sa.DateTime(timezone=True)), sa.Column("valid_until", sa.Date()), sa.Column("remarks", sa.Text()),
    )
    op.create_index("ix_credit_requests_organization_id", "credit_requests", ["organization_id"])
    op.create_index("ix_credit_requests_customer_id", "credit_requests", ["customer_id"])
    op.create_index("ix_credit_requests_status", "credit_requests", ["status"])

    op.create_table(
        "credit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("organization_id", "organizations.id", "CASCADE", False), _fk("customer_id", "customers.id", "CASCADE", False),
        sa.Column("facility_id", sa.Integer()), sa.Column("request_id", sa.Integer()), sa.Column("action", sa.String(40), nullable=False),
        _fk("user_id", "users.id", "SET NULL"), sa.Column("detail", sa.JSON(), nullable=False), _ts("created_at"),
    )
    op.create_index("ix_credit_events_organization_id", "credit_events", ["organization_id"])
    op.create_index("ix_credit_events_customer_id", "credit_events", ["customer_id"])

    op.add_column("shipments", sa.Column("party_branch_id", sa.Integer(), sa.ForeignKey("org_branches.id", ondelete="SET NULL")))


def downgrade():
    op.drop_column("shipments", "party_branch_id")
    for t in ("credit_events", "credit_requests", "credit_facilities", "org_contacts", "org_branches"):
        op.drop_table(t)
