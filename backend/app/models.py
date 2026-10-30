from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)
role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    license_number: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    is_demo: Mapped[bool] = mapped_column(default=False, server_default="false")
    logo: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(80), default="India")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", use_alter=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # --- license terms (License Management). NULL means "not limited": companies created before this module keep working exactly as before.
    license_start: Mapped[date | None] = mapped_column(Date)
    license_expiry: Mapped[date | None] = mapped_column(Date, index=True)
    renewal_cycle: Mapped[str | None] = mapped_column(String(10))          # MONTHLY | YEARLY
    max_users: Mapped[int | None] = mapped_column(Integer)                  # enabled user accounts allowed
    max_sessions: Mapped[int | None] = mapped_column(Integer)               # concurrent sessions allowed (needs session tracking)
    modules: Mapped[list | None] = mapped_column(JSON)                      # enabled module keys; NULL = every module
    contact: Mapped[dict | None] = mapped_column(JSON)                      # contact person, email, phone, address
    license_notes: Mapped[str | None] = mapped_column(Text)
    maintenance_plan: Mapped[str | None] = mapped_column(String(10))        # AMC (annual) | MMC (monthly) | NULL = none
    maintenance_expiry: Mapped[date | None] = mapped_column(Date)
    demo_portal: Mapped[bool] = mapped_column(default=False, server_default="false")   # a normal company whose users may also sign in on the Demo login page
    users: Mapped[list["User"]] = relationship(back_populates="organization", foreign_keys="User.organization_id")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    is_super_admin: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[str | None] = mapped_column(String(40))
    session_ip: Mapped[str | None] = mapped_column(String(60))
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    organization: Mapped[Organization | None] = relationship(back_populates="users", foreign_keys=[organization_id])
    roles: Mapped[list["Role"]] = relationship(secondary=user_roles, back_populates="users")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_role_org_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(secondary=role_permissions, back_populates="roles")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(240))
    module: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(40))
    roles: Mapped[list[Role]] = relationship(secondary=role_permissions, back_populates="permissions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    module: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_customer_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str | None] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(40), default="Business")
    country: Mapped[str] = mapped_column(String(80), default="India")
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    credit_limit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    payment_terms: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    roles: Mapped[list] = mapped_column(JSON, default=lambda: ["shipper"])
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Port(Base):
    __tablename__ = "ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    unlocode: Mapped[str | None] = mapped_column(String(10))
    port_type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(80))
    timezone: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Shipment(Base):
    __tablename__ = "shipments"

    __table_args__ = (UniqueConstraint("organization_id", "job_number", name="uq_shipment_org_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_number: Mapped[str] = mapped_column(String(40), index=True)
    job_type: Mapped[str] = mapped_column(String(20), default="Forwarding")
    mode: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20))
    container_mode: Mapped[str | None] = mapped_column(String(20))
    department: Mapped[str | None] = mapped_column(String(20))
    pol: Mapped[str | None] = mapped_column(String(80))
    pod: Mapped[str | None] = mapped_column(String(80))
    etd: Mapped[date | None] = mapped_column(Date)
    eta: Mapped[date | None] = mapped_column(Date)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    party_branch_id: Mapped[int | None] = mapped_column(ForeignKey("org_branches.id", ondelete="SET NULL"))
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), index=True)
    job_stage: Mapped[str] = mapped_column(String(40), default="Draft", server_default="Draft", index=True)
    stage_source: Mapped[str] = mapped_column(String(10), default="AUTO", server_default="AUTO")
    stage_reason: Mapped[str | None] = mapped_column(Text)
    stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN", index=True)
    closure_cycle: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    branch: Mapped[str | None] = mapped_column(String(80), index=True)            # the office that handles the job (Branch MIS)
    salesperson: Mapped[str | None] = mapped_column(String(120), index=True)      # who sold it (Salesperson MIS)
    parent_job_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), index=True)          # a CHA (customs) job points at its freight job; many CHA jobs can share one freight job and one booking
    cha_info: Mapped[dict | None] = mapped_column(JSON)                                                                      # customs details of a CHA job: file_ref, customs_location, cha_license, mbl, hbl, containers, exporter, importer
    customs_status: Mapped[str | None] = mapped_column(String(30))                                                            # CHA job stage: CREATED ... CLOSED
    consignee: Mapped[str | None] = mapped_column(String(160))
    packages: Mapped[int | None] = mapped_column(Integer)
    package_type: Mapped[str | None] = mapped_column(String(40))
    gross_weight: Mapped[float | None] = mapped_column(Numeric(12, 3))
    volume: Mapped[float | None] = mapped_column(Numeric(12, 3))
    commodity: Mapped[str | None] = mapped_column(String(160))
    bl_awb: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="Draft")
    revenue: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MasterRecord(Base):
    """Tenant-scoped master data (country, currency, unit, ...). Type-specific attributes live in `data`."""
    __tablename__ = "master_records"
    __table_args__ = (UniqueConstraint("organization_id", "master_type", "code", name="uq_master_org_type_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    master_type: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AccGroup(Base):
    """Chart of accounts group (main group or nested sub group). `code` is the ledger-code prefix of the group."""
    __tablename__ = "acc_groups"
    __table_args__ = (UniqueConstraint("organization_id", "parent_id", "name", name="uq_accgroup_org_parent_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("acc_groups.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(12))
    nature: Mapped[str] = mapped_column(String(20))   # ASSETS | LIABILITIES | INCOME | EXPENSES
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccLedger(Base):
    """A general ledger account under a group. Created by hand or by a master (party, charge, bank, employee)."""
    __tablename__ = "acc_ledgers"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_accledger_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("acc_groups.id", ondelete="RESTRICT"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    alias: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    opening_balance = mapped_column(Numeric(16, 2), default=0, server_default="0")
    opening_side: Mapped[str] = mapped_column(String(2), default="Dr")
    control: Mapped[bool] = mapped_column(Boolean, default=False)   # posted to by the system only (cash, bank, TDS ...)
    system_key: Mapped[str | None] = mapped_column(String(30))
    source_type: Mapped[str | None] = mapped_column(String(20))   # customer | party | charge | bank | employee
    source_id: Mapped[int | None] = mapped_column(Integer)
    source_role: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccVoucher(Base):
    """A balanced accounting voucher. System vouchers mirror a posted document or payment (source_type / source_id); journal, contra and opening vouchers are entered by hand."""
    __tablename__ = "acc_vouchers"
    __table_args__ = (UniqueConstraint("organization_id", "voucher_type", "number", name="uq_accvoucher_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    voucher_type: Mapped[str] = mapped_column(String(24), index=True)   # SALES DEBIT_NOTE CREDIT_NOTE PURCHASE PURCHASE_CREDIT_NOTE RECEIPT PAYMENT JOURNAL CONTRA
    number: Mapped[str] = mapped_column(String(40))
    vdate: Mapped[date] = mapped_column(Date, index=True)
    narration: Mapped[str | None] = mapped_column(String(400))
    party_name: Mapped[str | None] = mapped_column(String(200))
    job_number: Mapped[str | None] = mapped_column(String(40))
    source_type: Mapped[str | None] = mapped_column(String(20))   # document | payment
    source_id: Mapped[int | None] = mapped_column(Integer, index=True)
    total = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="POSTED", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entries: Mapped[list["AccEntry"]] = relationship(back_populates="voucher", cascade="all, delete-orphan", order_by="AccEntry.id")


class AccEntry(Base):
    __tablename__ = "acc_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("acc_vouchers.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("acc_ledgers.id", ondelete="RESTRICT"), index=True)
    debit = mapped_column(Numeric(16, 2), default=0, server_default="0")
    credit = mapped_column(Numeric(16, 2), default=0, server_default="0")
    narration: Mapped[str | None] = mapped_column(String(240))
    job_number: Mapped[str | None] = mapped_column(String(40))
    cleared_date: Mapped[date | None] = mapped_column(Date)   # bank reconciliation: the date the bank statement shows it
    voucher: Mapped[AccVoucher] = relationship(back_populates="entries")


class GstFiling(Base):
    """A GST return that has been filed on the portal (the company records the date and the acknowledgement number)."""
    __tablename__ = "gst_filings"
    __table_args__ = (UniqueConstraint("organization_id", "period", "return_type", name="uq_gst_filing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7))   # YYYY-MM of the supplies
    return_type: Mapped[str] = mapped_column(String(10))   # GSTR1 | GSTR3B
    filed_on: Mapped[date] = mapped_column(Date)
    ack_no: Mapped[str | None] = mapped_column(String(40))
    filed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FyClosing(Base):
    """A closed financial year (1 April - 31 March): the profit moved to reserves and the year locked against changes."""
    __tablename__ = "fy_closings"
    __table_args__ = (UniqueConstraint("organization_id", "fy_start", name="uq_fy_closing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    fy_start: Mapped[int] = mapped_column(Integer)   # the calendar year the FY starts in
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    net_profit = mapped_column(Numeric(16, 2), default=0, server_default="0")
    voucher_id: Mapped[int | None] = mapped_column(ForeignKey("acc_vouchers.id", ondelete="SET NULL"))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Gstr2bRow(Base):
    """One supplier invoice (or credit / debit note) of a GSTR-2B statement downloaded from the GST portal. Credit notes are stored with negative amounts."""
    __tablename__ = "gstr2b_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)   # YYYY-MM of the GSTR-2B
    gstin: Mapped[str] = mapped_column(String(15))
    supplier: Mapped[str | None] = mapped_column(String(200))
    doc_kind: Mapped[str] = mapped_column(String(10), default="INV")   # INV | CN | DN
    inv_no: Mapped[str] = mapped_column(String(60))
    inv_date: Mapped[date | None] = mapped_column(Date)
    taxable = mapped_column(Numeric(16, 2), default=0)
    cgst = mapped_column(Numeric(16, 2), default=0)
    sgst = mapped_column(Numeric(16, 2), default=0)
    igst = mapped_column(Numeric(16, 2), default=0)
    itc_available: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Airline(Base):
    """Airline master (the company's list of airlines / air carriers). Linked to the Organization Master record of the airline where one exists."""
    __tablename__ = "airlines"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_airline_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(10))   # airline code, e.g. UL
    name: Mapped[str] = mapped_column(String(200))
    sub_name: Mapped[str | None] = mapped_column(String(200))
    num_code: Mapped[str | None] = mapped_column(String(10))   # IATA numeric (AWB) prefix, e.g. 603
    icao_code: Mapped[str | None] = mapped_column(String(10))
    ac_code: Mapped[str | None] = mapped_column(String(20))
    myc_commission = mapped_column(Numeric(7, 2), default=0, server_default="0")
    country: Mapped[str | None] = mapped_column(String(80))
    address1: Mapped[str | None] = mapped_column(String(200))
    address2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    role_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")   # switched off because the organization no longer plays the role
    source_id: Mapped[int | None] = mapped_column(Integer)   # AIRLINE_ID of the file it was loaded from
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ShippingLine(Base):
    """Sea line (shipping line) master."""
    __tablename__ = "shipping_lines"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_shipline_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(20))   # SCAC / line code
    name: Mapped[str] = mapped_column(String(200))
    sub_name: Mapped[str | None] = mapped_column(String(200))
    scac_code: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str | None] = mapped_column(String(80))
    address1: Mapped[str | None] = mapped_column(String(200))
    address2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    website: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    role_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IataRecord(Base):
    """IATA master: an IATA agent / accreditation of an organization, optionally tied to an airline."""
    __tablename__ = "iata_records"
    __table_args__ = (UniqueConstraint("organization_id", "iata_code", name="uq_iata_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    airline_id: Mapped[int | None] = mapped_column(ForeignKey("airlines.id", ondelete="SET NULL"), index=True)
    iata_code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    cass_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(80))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    role_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CfsRecord(Base):
    """CFS master: a container freight station."""
    __tablename__ = "cfs_records"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_cfs_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(80))
    port_code: Mapped[str | None] = mapped_column(String(10))   # UNLOCODE of the port it serves
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    role_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AirlineStockLine(Base):
    """An AWB stock range received from an airline (master or house AWB numbers)."""
    __tablename__ = "airline_stock_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    airline_id: Mapped[int] = mapped_column(ForeignKey("airlines.id", ondelete="RESTRICT"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)   # the shipper organization the stock is held for, if any
    bill_type: Mapped[str] = mapped_column(String(10), default="MASTER")   # MASTER | HOUSE
    from_awb: Mapped[str] = mapped_column(String(12))
    to_awb: Mapped[str] = mapped_column(String(12))
    shipper: Mapped[str | None] = mapped_column(String(200))
    myc_value = mapped_column(Numeric(14, 2), default=0, server_default="0")
    cancel_charge = mapped_column(Numeric(14, 2), default=0, server_default="0")
    received_date: Mapped[date | None] = mapped_column(Date)
    ref_no: Mapped[str | None] = mapped_column(String(40))
    remarks: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CompanyDocProfile(Base):
    """Letterhead and registration details printed on the company's documents (BL / MTD): address, contacts, OTL / MTO / FMC registration numbers, signatory, signature and seal images."""
    __tablename__ = "company_doc_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class JobDetail(Base):
    """What the user fills in on the job screens: parties, cargo, containers, air dimensions and the BL. One row per job; each section is saved on its own."""
    __tablename__ = "job_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), unique=True)
    parties: Mapped[dict] = mapped_column(JSON, default=dict)
    cargo: Mapped[dict] = mapped_column(JSON, default=dict)
    containers: Mapped[list] = mapped_column(JSON, default=list)
    dims: Mapped[dict] = mapped_column(JSON, default=dict)
    bl: Mapped[dict] = mapped_column(JSON, default=dict)
    impdocs: Mapped[dict] = mapped_column(JSON, default=dict)
    saved_sections: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HblCounter(Base):
    __tablename__ = "hbl_counters"
    __table_args__ = (UniqueConstraint("organization_id", "hbl_type", name="uq_hbl_counter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    hbl_type: Mapped[str] = mapped_column(String(6))
    last_no: Mapped[int] = mapped_column(Integer, default=0)


class ChaFile(Base):
    """A document uploaded on a CHA job (Job History > Job Documents)."""
    __tablename__ = "cha_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomsBill(Base):
    """One customs clearance bill: a Shipping Bill (export) or a Bill of Entry (import). A booking / job can carry many; none is ever overwritten."""
    __tablename__ = "customs_bills"
    __table_args__ = (UniqueConstraint("organization_id", "bill_type", "bill_no", name="uq_customs_bill_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    bill_type: Mapped[str] = mapped_column(String(2))
    bill_no: Mapped[str] = mapped_column(String(40))
    bill_date: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(120))
    port_code: Mapped[str | None] = mapped_column(String(20))
    assessable_value: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    duty_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    examination: Mapped[str | None] = mapped_column(String(40))
    ooc_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="Filed")
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class JobCharge(Base):
    """One buy/sell charge line of a job. Amounts are always recalculated server-side from these inputs."""
    __tablename__ = "job_charges"
    __table_args__ = (UniqueConstraint("shipment_id", "line_no", name="uq_job_charge_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    service: Mapped[str] = mapped_column(String(20), default="Freight", server_default="Freight")
    charge_name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(String(240), default="")
    uom: Mapped[str] = mapped_column(String(40), default="Per Shipment")
    qty = mapped_column(Numeric(14, 3), default=1)
    hsn: Mapped[str] = mapped_column(String(20), default="")
    buy_ccy: Mapped[str] = mapped_column(String(3), default="INR")
    buy_exch = mapped_column(Numeric(14, 6), default=1)
    buy_rate = mapped_column(Numeric(16, 4), default=0)
    buy_tax_type: Mapped[str] = mapped_column(String(20), default="Taxable")
    buy_tax_pct = mapped_column(Numeric(6, 2), default=0)
    vendor: Mapped[str] = mapped_column(String(160), default="")
    use_vendor: Mapped[bool] = mapped_column(default=False)
    sell_ccy: Mapped[str] = mapped_column(String(3), default="INR")
    sell_exch = mapped_column(Numeric(14, 6), default=1)
    sell_rate = mapped_column(Numeric(16, 4), default=0)
    sell_tax_type: Mapped[str] = mapped_column(String(20), default="Taxable")
    sell_tax_pct = mapped_column(Numeric(6, 2), default=0)
    client: Mapped[str] = mapped_column(String(160), default="")
    use_client: Mapped[bool] = mapped_column(default=False)
    breakup: Mapped[list | None] = mapped_column(JSON)       # container-wise break-up: [{type, qty, sell, buy}] (the line then carries qty 1 and the totals as its rates)
    buy_pct = mapped_column(Numeric(8, 3), nullable=True)    # a "% of Freight" / "% of Other Charges" line: the percentage entered (the rates then hold the calculated amounts, quantity 1)
    sell_pct = mapped_column(Numeric(8, 3), nullable=True)
    qty_manual: Mapped[bool] = mapped_column(default=False, server_default="false")      # the quantity was typed by the user and is not recalculated from the Basis
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DocSequence(Base):
    """Gap-free per-tenant, per-type, per-fiscal-year document counters (row-locked when incremented)."""
    __tablename__ = "doc_sequences"
    __table_args__ = (UniqueConstraint("organization_id", "seq_type", "fiscal_year", name="uq_doc_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    seq_type: Mapped[str] = mapped_column(String(30))
    fiscal_year: Mapped[str] = mapped_column(String(7))
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base):
    """Invoice / purchase invoice / debit note / credit note. Numbers are assigned at posting; posted documents are never edited."""
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "doc_type", "doc_number", name="uq_document_number"),
        CheckConstraint("paid_amount >= 0 AND credited_amount >= 0", name="ck_document_amounts_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30), index=True)
    doc_number: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    doc_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), index=True)
    job_number: Mapped[str | None] = mapped_column(String(40))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    party_name: Mapped[str] = mapped_column(String(200), default="")
    party_gstin: Mapped[str | None] = mapped_column(String(15))
    place_of_supply: Mapped[str | None] = mapped_column(String(60))
    supply_type: Mapped[str] = mapped_column(String(10), default="inter")
    ref_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    remarks: Mapped[str | None] = mapped_column(Text)
    header: Mapped[dict] = mapped_column(JSON, default=dict)
    subtotal = mapped_column(Numeric(16, 2), default=0)
    tax_total = mapped_column(Numeric(16, 2), default=0)
    grand_total = mapped_column(Numeric(16, 2), default=0)
    paid_amount = mapped_column(Numeric(16, 2), default=0)
    credited_amount = mapped_column(Numeric(16, 2), default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    lines: Mapped[list["DocumentLine"]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="DocumentLine.line_no")


class DocumentLine(Base):
    __tablename__ = "document_lines"
    __table_args__ = (UniqueConstraint("document_id", "line_no", name="uq_document_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    charge_name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(String(240), default="")
    job_number: Mapped[str | None] = mapped_column(String(40), index=True)   # the job this line belongs to (one invoice can bill several jobs of the same party)
    hsn: Mapped[str] = mapped_column(String(20), default="")
    uom: Mapped[str] = mapped_column(String(40), default="Per Shipment")
    qty = mapped_column(Numeric(14, 3), default=1)
    ccy: Mapped[str] = mapped_column(String(3), default="INR")
    exch = mapped_column(Numeric(14, 6), default=1)
    rate = mapped_column(Numeric(16, 4), default=0)
    tax_type: Mapped[str] = mapped_column(String(20), default="Taxable")
    tax_pct = mapped_column(Numeric(6, 2), default=0)
    amount = mapped_column(Numeric(16, 2), default=0)
    tax_amount = mapped_column(Numeric(16, 2), default=0)
    line_total = mapped_column(Numeric(16, 2), default=0)
    document: Mapped[Document] = relationship(back_populates="lines")


class Payment(Base):
    """Money received (RECEIPT, against AR documents) or paid (PAYMENT, against AP documents)."""
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "pay_number", name="uq_payment_number"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_idempotency"),
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    pay_number: Mapped[str] = mapped_column(String(40))
    pay_date: Mapped[date] = mapped_column(Date)
    party_name: Mapped[str] = mapped_column(String(200), default="")
    method: Mapped[str] = mapped_column(String(30), default="Bank Transfer")
    reference: Mapped[str | None] = mapped_column(String(80))
    amount = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(20), default="POSTED")
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    remarks: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)   # transaction details: cheque no / date / bank, UTR, deposit or paying account, settlement FULL / PART
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="payment", cascade="all, delete-orphan")


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_allocation_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    amount = mapped_column(Numeric(16, 2))
    round_off = mapped_column(Numeric(16, 2), default=0, server_default="0")   # a small remainder written off to close the document (an adjustment, never cash)
    tds_amount = mapped_column(Numeric(16, 2), default=0, server_default="0")   # tax deducted at source by the customer: settles the invoice but is not cash received
    payment: Mapped[Payment] = relationship(back_populates="allocations")


class Quotation(Base):
    """Sales quotation: DRAFT (editable) -> SENT (numbered, locked) -> ACCEPTED/REJECTED -> CONVERTED to a job."""
    __tablename__ = "quotations"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_quotation_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    quote_date: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    party_name: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), default="Sea")
    direction: Mapped[str] = mapped_column(String(20), default="Export")
    shipment_type: Mapped[str | None] = mapped_column(String(40))
    quote_type: Mapped[str | None] = mapped_column(String(60))
    carrier_mode: Mapped[str] = mapped_column(String(10), default="single")
    pol: Mapped[str | None] = mapped_column(String(80))
    pod: Mapped[str | None] = mapped_column(String(80))
    terms: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    header: Mapped[dict] = mapped_column(JSON, default=dict)
    job_number: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approval_status: Mapped[str] = mapped_column(String(12), default="DRAFT")
    prepared_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_remarks: Mapped[str | None] = mapped_column(Text)
    root_id: Mapped[int | None] = mapped_column(Integer, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    lines: Mapped[list["QuotationLine"]] = relationship(back_populates="quotation", cascade="all, delete-orphan", order_by="QuotationLine.line_no")


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    __table_args__ = (UniqueConstraint("quotation_id", "line_no", name="uq_quotation_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    charge_name: Mapped[str] = mapped_column(String(160), default="")
    carrier: Mapped[str] = mapped_column(String(160), default="")
    basis: Mapped[str] = mapped_column(String(40), default="Per Shipment")
    ccy: Mapped[str] = mapped_column(String(3), default="INR")
    exch = mapped_column(Numeric(14, 6), default=1)
    rate = mapped_column(Numeric(16, 4), default=0)
    qty = mapped_column(Numeric(14, 3), default=1)
    tax_pct = mapped_column(Numeric(6, 2), default=0)
    amount = mapped_column(Numeric(16, 2), default=0)  # converted to base currency (INR), before GST
    side: Mapped[str] = mapped_column(String(4), default="SELL")
    taxable: Mapped[bool] = mapped_column(default=False)
    container_type: Mapped[str | None] = mapped_column(String(40))
    container_breakup: Mapped[list | None] = mapped_column(JSON)
    qty_manual: Mapped[bool] = mapped_column(default=False, server_default="false")      # the quantity was typed by the user: a calculated Basis does not overwrite it
    remarks: Mapped[str] = mapped_column(String(500), default="")
    amount_fcy = mapped_column(Numeric(16, 2), default=0)  # in the line's own currency
    gst_amount = mapped_column(Numeric(16, 2), default=0)  # base currency
    total_inr = mapped_column(Numeric(16, 2), default=0)  # amount + GST, base currency
    quotation: Mapped[Quotation] = relationship(back_populates="lines")


class QuotationEvent(Base):
    """Immutable audit trail of quotation workflow steps."""
    __tablename__ = "quotation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str | None] = mapped_column(String(30))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(Base):
    """Carrier booking: DRAFT (editable) -> CONFIRMED (numbered, job created) -> CANCELLED / SPACE_RELEASED."""
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_booking_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    booking_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(20), default="Sea")
    direction: Mapped[str] = mapped_column(String(20), default="Export")
    shipment_type: Mapped[str | None] = mapped_column(String(40))
    booking_type: Mapped[str | None] = mapped_column(String(20))
    hbl_type: Mapped[str | None] = mapped_column(String(60))
    scac: Mapped[str | None] = mapped_column(String(20))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    party_name: Mapped[str] = mapped_column(String(200), default="")
    carrier: Mapped[str | None] = mapped_column(String(160))
    carrier_booking_no: Mapped[str | None] = mapped_column(String(80))
    vessel: Mapped[str | None] = mapped_column(String(120))
    pol: Mapped[str | None] = mapped_column(String(80))
    pod: Mapped[str | None] = mapped_column(String(80))
    etd: Mapped[date | None] = mapped_column(Date)
    eta: Mapped[date | None] = mapped_column(Date)
    si_cutoff: Mapped[date | None] = mapped_column(Date)
    vgm_cutoff: Mapped[date | None] = mapped_column(Date)
    job_number: Mapped[str | None] = mapped_column(String(40), index=True)
    quotation_id: Mapped[int | None] = mapped_column(ForeignKey("quotations.id", ondelete="SET NULL"), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CrmRecord(Base):
    """CRM working records (lead / enquiry / opportunity / followup / booking). The document is kept as JSON; `ref` is the CRM number."""
    __tablename__ = "crm_records"
    __table_args__ = (UniqueConstraint("organization_id", "kind", "ref", name="uq_crm_record_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    ref: Mapped[str] = mapped_column(String(40))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(40), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrgBranch(Base):
    """A branch of a party (Organization Master record). Never deleted once used; deactivate instead."""
    __tablename__ = "org_branches"
    __table_args__ = (UniqueConstraint("customer_id", "code", name="uq_org_branch_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    state: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(400))
    pincode: Mapped[str | None] = mapped_column(String(12))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    gstin: Mapped[str | None] = mapped_column(String(30))
    sl_no: Mapped[int | None] = mapped_column(Integer)
    address2: Mapped[str | None] = mapped_column(String(200))
    country_code: Mapped[str | None] = mapped_column(String(5))
    state_code: Mapped[str | None] = mapped_column(String(5))
    tin_no: Mapped[str | None] = mapped_column(String(30))
    pan_no: Mapped[str | None] = mapped_column(String(10))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    reg_no: Mapped[str | None] = mapped_column(String(40))
    fax: Mapped[str | None] = mapped_column(String(40))
    branch_type: Mapped[str | None] = mapped_column(String(30))
    bin_no: Mapped[str | None] = mapped_column(String(40))
    adcode: Mapped[str | None] = mapped_column(String(40))
    remarks: Mapped[str | None] = mapped_column(String(500))
    gstn_type: Mapped[str | None] = mapped_column(String(60))
    billing_enabled: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrgContact(Base):
    __tablename__ = "org_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("org_branches.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    designation: Mapped[str | None] = mapped_column(String(80))
    department: Mapped[str | None] = mapped_column(String(40))
    mobile: Mapped[str | None] = mapped_column(String(40))
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    landline: Mapped[str | None] = mapped_column(String(40))
    is_primary: Mapped[bool] = mapped_column(default=False)
    is_billing: Mapped[bool] = mapped_column(default=False)
    is_operational: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CreditFacility(Base):
    """A credit application / approved facility. Approved facilities are immutable: changes are new REVISION or RENEWAL rows."""
    __tablename__ = "credit_facilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="NEW")
    status: Mapped[str] = mapped_column(String(14), default="DRAFT", index=True)
    credit_days: Mapped[int] = mapped_column(Integer, default=0)
    requested_limit = mapped_column(Numeric(16, 2), default=0)
    approved_limit = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    agreement_no: Mapped[str | None] = mapped_column(String(80))
    agreement_file: Mapped[str | None] = mapped_column(String(240))
    policy: Mapped[str] = mapped_column(String(6), default="SOFT")
    branch_limits: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    prev_id: Mapped[int | None] = mapped_column(ForeignKey("credit_facilities.id", ondelete="SET NULL"))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    approval_remarks: Mapped[str | None] = mapped_column(Text)


class CreditRequest(Base):
    """Grace period / extension / soft-block override / hard-block release, each needing management approval."""
    __tablename__ = "credit_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    facility_id: Mapped[int | None] = mapped_column(ForeignKey("credit_facilities.id", ondelete="SET NULL"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("org_branches.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="PENDING", index=True)
    days: Mapped[int | None] = mapped_column(Integer)
    amount = mapped_column(Numeric(16, 2))
    one_time: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[date | None] = mapped_column(Date)
    remarks: Mapped[str | None] = mapped_column(Text)


class CreditEvent(Base):
    __tablename__ = "credit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    facility_id: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobEvent(Base):
    """Append-only job activity log (the database refuses UPDATEs). One row per meaningful business event."""
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), index=True)
    job_number: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(50))
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(String(12), default="USER")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(20), default="API")
    reason: Mapped[str | None] = mapped_column(Text)
    related_type: Mapped[str | None] = mapped_column(String(30))
    related_id: Mapped[str | None] = mapped_column(String(60))
    correlation_id: Mapped[str | None] = mapped_column(String(60))
    approval_ref: Mapped[str | None] = mapped_column(String(60))
    result: Mapped[str] = mapped_column(String(10), default="SUCCESS")
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobStageHistory(Base):
    __tablename__ = "job_stage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), index=True)
    job_number: Mapped[str] = mapped_column(String(40))
    previous_stage: Mapped[str | None] = mapped_column(String(40))
    new_stage: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(10))
    trigger: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    related_ref: Mapped[str | None] = mapped_column(String(80))
    approval_ref: Mapped[str | None] = mapped_column(String(60))
    event_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobClosure(Base):
    """One row per successful closure / reopening; previous rows are never changed."""
    __tablename__ = "job_closures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), index=True)
    job_number: Mapped[str] = mapped_column(String(40))
    cycle_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    previous_status: Mapped[str] = mapped_column(String(10))
    new_status: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    approval_ref: Mapped[str | None] = mapped_column(String(60))
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    final_buy = mapped_column(Numeric(16, 2))
    final_sell = mapped_column(Numeric(16, 2))
    final_margin = mapped_column(Numeric(16, 2))
    previous_closure_id: Mapped[int | None] = mapped_column(Integer)
    event_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobMilestone(Base):
    """An ACTUAL operational milestone confirmed by a user (planned dates never count)."""
    __tablename__ = "job_milestones"
    __table_args__ = (UniqueConstraint("shipment_id", "code", name="uq_job_milestone"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    actual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    remarks: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SequenceConfig(Base):
    """A numbering sequence (format + scope + reset rules). Active configs are immutable: changes are a new version."""
    __tablename__ = "sequence_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    module: Mapped[str] = mapped_column(String(40))
    doc_type: Mapped[str] = mapped_column(String(40))
    branch_scope: Mapped[str] = mapped_column(String(20), default="ALL")
    fy_scope: Mapped[str] = mapped_column(String(12), default="ALL")
    shipment_scope: Mapped[str] = mapped_column(String(40), default="ALL")
    components: Mapped[list] = mapped_column(JSON, default=list)
    number_width: Mapped[int] = mapped_column(Integer, default=5)
    start_number = mapped_column(BigInteger, default=1)
    step: Mapped[int] = mapped_column(Integer, default=1)
    reset_policy: Mapped[str] = mapped_column(String(10), default="FY")
    max_length: Mapped[int] = mapped_column(Integer, default=40)
    doc_code: Mapped[str | None] = mapped_column(String(12))
    fy_format: Mapped[str] = mapped_column(String(10), default="YY-YY")
    shipment_codes: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("sequence_configs.id", ondelete="SET NULL"))
    is_system_default: Mapped[bool] = mapped_column(Boolean, default=False)
    remarks: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SequenceCounter(Base):
    __tablename__ = "sequence_counters"
    __table_args__ = (UniqueConstraint("config_id", "period_key", name="uq_sequence_counter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("sequence_configs.id", ondelete="CASCADE"), index=True)
    period_key: Mapped[str] = mapped_column(String(12))
    last_number = mapped_column(BigInteger, default=0)


class IssuedNumber(Base):
    """Registry of every centrally issued identifier: the final guard against duplicates and the trace back to the configuration."""
    __tablename__ = "issued_numbers"
    __table_args__ = (UniqueConstraint("organization_id", "doc_type", "number", name="uq_issued_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    number: Mapped[str] = mapped_column(String(60))
    config_id: Mapped[int | None] = mapped_column(ForeignKey("sequence_configs.id", ondelete="SET NULL"))
    config_version: Mapped[int | None] = mapped_column(Integer)
    period_key: Mapped[str | None] = mapped_column(String(12))
    branch_code: Mapped[str | None] = mapped_column(String(20))
    shipment_key: Mapped[str | None] = mapped_column(String(40))
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    related_type: Mapped[str | None] = mapped_column(String(30))
    related_id: Mapped[str | None] = mapped_column(String(60))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SequenceEvent(Base):
    __tablename__ = "sequence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("sequence_configs.id", ondelete="SET NULL"), index=True)
    doc_type: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40))
    previous: Mapped[dict | None] = mapped_column(JSON)
    new: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    related_ref: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[str] = mapped_column(String(10), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LicenseRenewal(Base):
    """One renewal of a company license. The request_id makes a repeated request (double click, retry) return the same renewal instead of extending twice."""
    __tablename__ = "license_renewals"
    __table_args__ = (UniqueConstraint("organization_id", "request_id", name="uq_license_renewal_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    cycle: Mapped[str] = mapped_column(String(10))
    previous_expiry: Mapped[date | None] = mapped_column(Date)
    new_expiry: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(120))              # free text (invoice / receipt no); a renewal is not proof of payment
    performed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(12), default="LICENSE", server_default="LICENSE")      # LICENSE | MAINTENANCE (AMC / MMC)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    payment_method: Mapped[str | None] = mapped_column(String(20))
    payment_status: Mapped[str] = mapped_column(String(10), default="PENDING", server_default="PENDING")   # what the administrator recorded: PENDING | RECEIVED | WAIVED
    payment_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LicenseNotification(Base):
    """An expiry reminder for one license cycle. Unique per (company, reminder, expiry date): the same reminder is never created twice."""
    __tablename__ = "license_notifications"
    __table_args__ = (UniqueConstraint("organization_id", "kind", "expiry_date", name="uq_license_notice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))                           # D30 | D7 | D1 | D0 | OVERDUE
    expiry_date: Mapped[date] = mapped_column(Date)
    message: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")         # OPEN | ACKNOWLEDGED
    channel_status: Mapped[str] = mapped_column(String(20), default="IN_APP")  # IN_APP | EMAIL_SENT | EMAIL_FAILED | EMAIL_NOT_CONFIGURED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserUsage(Base):
    """How long a user was active on a day (one row per user and day), used by License Management for online time and top users."""
    __tablename__ = "user_usage"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_usage_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HblClause(Base):
    """HBL Clause Master: a wording that can be printed on a House BL. Selections are snapshotted on the BL, so editing a clause never changes a saved or released HBL."""
    __tablename__ = "hbl_clauses"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_hbl_clause_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default="Other")
    shipment_type: Mapped[str] = mapped_column(String(4), default="Both")
    movement_type: Mapped[str] = mapped_column(String(20), default="All")
    country: Mapped[str | None] = mapped_column(String(80))
    pol: Mapped[str | None] = mapped_column(String(80))
    pod: Mapped[str | None] = mapped_column(String(80))
    freight_term: Mapped[str] = mapped_column(String(8), default="Both")
    is_default: Mapped[bool] = mapped_column(default=False)
    is_mandatory: Mapped[bool] = mapped_column(default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    exclusive_group: Mapped[str | None] = mapped_column(String(40))       # clauses of one group cannot be on the same HBL (e.g. FCL vs LCL, Prepaid vs Collect)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class JobLock(Base):
    """The one user editing a job right now (a lease renewed by heartbeats; abandoned locks expire)."""
    __tablename__ = "job_locks"
    __table_args__ = (UniqueConstraint("organization_id", "job_number", name="uq_job_locks_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_number: Mapped[str] = mapped_column(String(60))
    job_type: Mapped[str] = mapped_column(String(30), default="Other")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_name: Mapped[str] = mapped_column(String(160))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    tabs: Mapped[dict] = mapped_column(JSON, default=dict)        # browser tab id -> last heartbeat (ISO time): one tab closing does not free the lock while another tab of the same user is open


class JobLockRequest(Base):
    __tablename__ = "job_lock_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_number: Mapped[str] = mapped_column(String(60), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    requester_name: Mapped[str] = mapped_column(String(160))
    holder_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobLockEvent(Base):
    """Audit trail of lock acquisition, release, expiry, access requests and takeovers; notify_user_id rows are shown once to that user."""
    __tablename__ = "job_lock_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_number: Mapped[str] = mapped_column(String(60), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_name: Mapped[str] = mapped_column(String(160), default="")
    event: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text, default="")
    notify_user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    seen: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
