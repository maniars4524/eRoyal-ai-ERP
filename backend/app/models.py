from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Table, Text, UniqueConstraint, func
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
