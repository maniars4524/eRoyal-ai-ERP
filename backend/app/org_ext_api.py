"""Organization Master extensions: branches, branch-wise contacts, accounting classification checks and the credit facility workflow.

Credit workflow: DRAFT -> SUBMITTED -> UNDER_REVIEW -> APPROVED / REJECTED. Approved facilities are never edited:
changes are a new REVISION or RENEWAL that supersedes the old one when approved. Grace / extension / override / release
requests need management approval (credit.approve; overrides also credit.override). Nobody approves their own request
(except in demo companies).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import credit_control as cc
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import CreditEvent, CreditFacility, CreditRequest, Customer, Document, MasterRecord, OrgBranch, OrgContact, Organization, Shipment, User

router = APIRouter(tags=["organization-master"])
ZERO = Decimal(0)
DEPARTMENTS = "^(Sales|Finance|Operations|Management|Documentation|Other)$"
CLASSES = ("customer", "vendor", "both", "other")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization Master is tenant-scoped")
    return user.organization_id


def _party(db: Session, user: User, customer_id: int) -> Customer:
    c = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == _tenant(user)))
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return c


def _money(v: Any, name: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"{name} must be a number")
    if d < 0:
        raise HTTPException(422, f"{name} cannot be negative")
    return d


def _demo(db: Session, user: User) -> bool:
    org = db.get(Organization, user.organization_id)
    return bool(org and org.is_demo)


# ---------------------------------------------------------------- accounting classification
def validate_accounting(db: Session, org_id: int, data: dict) -> None:
    """Called by the organization save: control accounts must exist (active) in the same company's Chart of Accounts."""
    klass = str(data.get("acct_class", "") or "").lower()
    if klass and klass not in CLASSES:
        raise HTTPException(422, "Accounting classification must be customer, vendor, both or other")
    need = {"customer": ["ar_account"], "vendor": ["ap_account"], "both": ["ar_account", "ap_account"]}.get(klass, [])
    for key in need:
        if not str(data.get(key, "") or "").strip():
            raise HTTPException(422, f"{'AR' if key == 'ar_account' else 'AP'} control account is required for a {klass} classification")
    for key in ("ar_account", "ap_account"):
        code = str(data.get(key, "") or "").strip()
        if code and not db.scalar(select(MasterRecord.id).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "account", MasterRecord.code == code, MasterRecord.status == "ACTIVE")):
            raise HTTPException(422, f"Account {code} does not exist in this company's Chart of Accounts")


# ---------------------------------------------------------------- branches
GSTN_TYPES = ("G/GSN - GSTIN of Normal Persons", "S/SEZ - GSTIN of SEZ Unit", "D/SEZD - GSTIN of SEZ Developer", "U/UIN - UIN Holder (embassy, UN body)", "T/TDS - GSTIN of TDS Deductor",
              "C/TCS - GSTIN of TCS Collector", "N/NRTP - Non-resident Taxable Person", "O/OIDAR - Online Service Provider", "R/UNREG - Unregistered / Not applicable")
BRANCH_TYPES = ("OTHERS", "HEAD OFFICE", "REGISTERED OFFICE", "BRANCH OFFICE", "FACTORY", "WAREHOUSE", "SEZ UNIT")
_OPT = lambda n, m=80: Field(default=None, max_length=m)


class BranchIn(BaseModel):
    code: str | None = Field(default=None, max_length=20, pattern=r"^[A-Za-z0-9._/-]*$")
    name: str = Field(min_length=1, max_length=160)
    address: str | None = Field(default=None, max_length=400)  # Address 1
    address2: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, max_length=5)
    state: str | None = Field(default=None, max_length=80)
    state_code: str | None = Field(default=None, max_length=5)
    city: str | None = Field(default=None, max_length=80)
    pincode: str | None = Field(default=None, max_length=12)
    contact_person: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    fax: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    tin_no: str | None = Field(default=None, max_length=30)
    pan_no: str | None = Field(default=None, max_length=10)
    gstin: str | None = Field(default=None, max_length=30)  # GSTN ID
    reg_no: str | None = Field(default=None, max_length=40)
    branch_type: str | None = Field(default=None, max_length=30)
    bin_no: str | None = Field(default=None, max_length=40)
    adcode: str | None = Field(default=None, max_length=40)
    gstn_type: str | None = Field(default=None, max_length=60)
    remarks: str | None = Field(default=None, max_length=500)
    billing_enabled: bool = False
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v):
        v = (v or "").strip().upper()
        return v or None

    @field_validator("pan_no", "gstin", mode="before")
    @classmethod
    def _upper(cls, v):
        v = (v or "").strip().upper()
        return v or None


class BranchPatch(BranchIn):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    billing_enabled: bool | None = None


BRANCH_FIELDS = ("id", "customer_id", "sl_no", "code", "name", "address", "address2", "country", "country_code", "state", "state_code", "city", "pincode", "contact_person", "phone", "fax", "email", "tin_no", "pan_no", "gstin",
                 "reg_no", "branch_type", "bin_no", "adcode", "gstn_type", "remarks", "billing_enabled", "status")


def _branch_out(b: OrgBranch) -> dict:
    return {c: getattr(b, c) for c in BRANCH_FIELDS}


def _check_branch_fields(b: OrgBranch) -> None:
    indian = (b.country or "India").strip().lower() in ("india", "in", "")
    if b.gstin and indian and not re.fullmatch(r"[0-9A-Z]{15}", b.gstin):
        raise HTTPException(422, "GSTN ID must be 15 letters/digits")
    if b.pan_no and not re.fullmatch(r"[0-9A-Z]{10}", b.pan_no):
        raise HTTPException(422, "PAN No must be 10 letters/digits")
    if b.billing_enabled and not (b.address and (b.gstin or not indian)):
        raise HTTPException(422, "A billing-enabled branch needs Address 1 and a GSTN ID (overseas branches need only the address)")


@router.get("/api/customers/{customer_id}/branches")
def list_branches(customer_id: int, user: User = Depends(permission_required("customer.read")), db: Session = Depends(get_db)):
    _party(db, user, customer_id)
    return [_branch_out(b) for b in db.scalars(select(OrgBranch).where(OrgBranch.customer_id == customer_id).order_by(OrgBranch.sl_no, OrgBranch.id)).all()]


@router.get("/api/org-options/branches")
def branch_options(user: User = Depends(permission_required("customer.read"))):
    return {"gstn_types": GSTN_TYPES, "branch_types": BRANCH_TYPES}


@router.post("/api/customers/{customer_id}/branches", status_code=201)
def add_branch(customer_id: int, body: BranchIn, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    db.execute(select(Customer.id).where(Customer.id == c.id).with_for_update())  # serialise serial-number assignment per organization
    sl_no = (db.scalar(select(func.coalesce(func.max(OrgBranch.sl_no), 0)).where(OrgBranch.customer_id == c.id)) or 0) + 1
    data = body.model_dump()
    data["code"] = data["code"] or (re.sub(r"[^A-Z0-9]", "", (body.name or "BR").upper())[:6] or "BR") + str(sl_no)
    if db.scalar(select(OrgBranch.id).where(OrgBranch.customer_id == c.id, OrgBranch.code == data["code"])):
        raise HTTPException(409, f"Branch code {data['code']} already exists for this organization")
    b = OrgBranch(organization_id=c.organization_id, customer_id=c.id, created_by=user.id, sl_no=sl_no, **data)
    _check_branch_fields(b)
    db.add(b)
    db.flush()
    record_audit(db, "ORG_BRANCH_ADDED", "ORGANIZATION", user, c.organization_id, "OrgBranch", str(b.id), {"code": b.code, "sl_no": sl_no, "billing_enabled": b.billing_enabled})
    db.commit()
    return _branch_out(b)


@router.patch("/api/customers/{customer_id}/branches/{branch_id}")
def edit_branch(customer_id: int, branch_id: int, body: BranchPatch, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    b = db.scalar(select(OrgBranch).where(OrgBranch.id == branch_id, OrgBranch.customer_id == c.id))
    if not b:
        raise HTTPException(404, "Branch not found")
    changes = body.model_dump(exclude_unset=True)
    changes.pop("code", None)  # the branch code is permanent: documents and credit limits refer to it
    for k, v in changes.items():
        setattr(b, k, v)
    _check_branch_fields(b)
    record_audit(db, "ORG_BRANCH_UPDATED", "ORGANIZATION", user, c.organization_id, "OrgBranch", str(b.id), changes)
    db.commit()
    return _branch_out(b)


@router.delete("/api/customers/{customer_id}/branches/{branch_id}", status_code=204)
def delete_branch(customer_id: int, branch_id: int, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    """Only a branch that was never used can be deleted. Used branches are deactivated instead (history stays intact)."""
    c = _party(db, user, customer_id)
    b = db.scalar(select(OrgBranch).where(OrgBranch.id == branch_id, OrgBranch.customer_id == c.id).with_for_update())
    if not b:
        raise HTTPException(404, "Branch not found")
    used = (db.scalar(select(func.count()).select_from(Shipment).where(Shipment.party_branch_id == b.id))
            or db.scalar(select(func.count()).select_from(Document).where(Document.customer_id == c.id, Document.header["party_branch_id"].as_string() == str(b.id)))
            or db.scalar(select(func.count()).select_from(CreditRequest).where(CreditRequest.branch_id == b.id))
            or any(str(x.get("branch_id")) == str(b.id) for f in db.scalars(select(CreditFacility).where(CreditFacility.customer_id == c.id)) for x in (f.branch_limits or [])))
    if used:
        raise HTTPException(409, "This branch is used by jobs, invoices or credit limits and cannot be deleted. Deactivate it instead")
    record_audit(db, "ORG_BRANCH_DELETED", "ORGANIZATION", user, c.organization_id, "OrgBranch", str(b.id), {"code": b.code, "name": b.name})
    db.delete(b)
    db.commit()


# ---------------------------------------------------------------- contacts
class ContactIn(BaseModel):
    branch_id: int | None = None
    name: str = Field(min_length=1, max_length=160)
    designation: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, pattern=DEPARTMENTS)
    mobile: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    landline: str | None = Field(default=None, max_length=40)
    is_primary: bool = False
    is_billing: bool = False
    is_operational: bool = False
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")


class ContactPatch(ContactIn):
    name: str | None = Field(default=None, min_length=1, max_length=160)


def _contact_out(c: OrgContact) -> dict:
    return {k: getattr(c, k) for k in ("id", "customer_id", "branch_id", "name", "designation", "department", "mobile", "whatsapp", "email", "landline", "is_primary", "is_billing", "is_operational", "status")}


def _check_branch(db: Session, customer: Customer, branch_id: int | None) -> None:
    if branch_id is not None and not db.scalar(select(OrgBranch.id).where(OrgBranch.id == branch_id, OrgBranch.customer_id == customer.id)):
        raise HTTPException(422, "Branch does not belong to this organization")


@router.get("/api/customers/{customer_id}/contacts")
def list_contacts(customer_id: int, branch_id: int | None = None, billing: bool | None = None, active_only: bool = False, user: User = Depends(permission_required("customer.read")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    q = select(OrgContact).where(OrgContact.customer_id == c.id)
    if branch_id is not None:
        q = q.where(OrgContact.branch_id == branch_id)
    if billing is not None:
        q = q.where(OrgContact.is_billing == billing)
    if active_only:
        q = q.where(OrgContact.status == "ACTIVE")
    return [_contact_out(x) for x in db.scalars(q.order_by(OrgContact.branch_id, OrgContact.id)).all()]


@router.post("/api/customers/{customer_id}/contacts", status_code=201)
def add_contact(customer_id: int, body: ContactIn, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    _check_branch(db, c, body.branch_id)
    row = OrgContact(organization_id=c.organization_id, customer_id=c.id, **body.model_dump())
    db.add(row)
    db.flush()
    record_audit(db, "ORG_CONTACT_ADDED", "ORGANIZATION", user, c.organization_id, "OrgContact", str(row.id), {"name": row.name, "branch_id": row.branch_id})
    db.commit()
    return _contact_out(row)


@router.patch("/api/customers/{customer_id}/contacts/{contact_id}")
def edit_contact(customer_id: int, contact_id: int, body: ContactPatch, user: User = Depends(permission_required("customer.update")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    row = db.scalar(select(OrgContact).where(OrgContact.id == contact_id, OrgContact.customer_id == c.id))
    if not row:
        raise HTTPException(404, "Contact not found")
    changes = body.model_dump(exclude_unset=True)
    if "branch_id" in changes:
        _check_branch(db, c, changes["branch_id"])
    for k, v in changes.items():
        setattr(row, k, v)
    record_audit(db, "ORG_CONTACT_UPDATED", "ORGANIZATION", user, c.organization_id, "OrgContact", str(row.id), changes)
    db.commit()
    return _contact_out(row)


# ---------------------------------------------------------------- credit facilities
class FacilityIn(BaseModel):
    kind: str = Field(default="NEW", pattern="^(NEW|REVISION|RENEWAL)$")
    credit_days: int = Field(default=0, ge=0, le=365)
    requested_limit: Decimal = Field(default=Decimal(0), ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    agreement_no: str | None = Field(default=None, max_length=80)
    agreement_file: str | None = Field(default=None, max_length=240)
    reason: str | None = Field(default=None, max_length=2000)
    branch_limits: list[dict[str, Any]] = Field(default_factory=list)


class FacilityPatch(BaseModel):
    credit_days: int | None = Field(default=None, ge=0, le=365)
    requested_limit: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    agreement_no: str | None = Field(default=None, max_length=80)
    agreement_file: str | None = Field(default=None, max_length=240)
    reason: str | None = Field(default=None, max_length=2000)
    branch_limits: list[dict[str, Any]] | None = None


class DecisionIn(BaseModel):
    approved_limit: Decimal | None = Field(default=None, gt=0)
    credit_days: int | None = Field(default=None, ge=0, le=365)
    start_date: date | None = None
    expiry_date: date | None = None
    policy: str | None = Field(default=None, pattern="^(SOFT|HARD)$")
    branch_limits: list[dict[str, Any]] | None = None
    remarks: str | None = Field(default=None, max_length=2000)


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: (f"{u.first_name} {u.last_name}".strip() or u.username) for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


def _facility_out(f: CreditFacility, names: dict | None = None) -> dict:
    names = names or {}
    return {"id": f.id, "customer_id": f.customer_id, "kind": f.kind, "status": f.status, "credit_days": f.credit_days, "requested_limit": str(f.requested_limit), "approved_limit": None if f.approved_limit is None else str(f.approved_limit),
            "currency": f.currency, "agreement_no": f.agreement_no, "agreement_file": f.agreement_file, "policy": f.policy, "branch_limits": f.branch_limits or [], "reason": f.reason, "prev_id": f.prev_id,
            "requested_by": names.get(f.requested_by), "requested_at": f.requested_at.isoformat() if f.requested_at else None, "submitted_at": f.submitted_at.isoformat() if f.submitted_at else None,
            "reviewed_by": names.get(f.reviewed_by), "approved_by": names.get(f.approved_by), "approved_at": f.approved_at.isoformat() if f.approved_at else None,
            "start_date": f.start_date.isoformat() if f.start_date else None, "expiry_date": f.expiry_date.isoformat() if f.expiry_date else None, "approval_remarks": f.approval_remarks}


def _request_out(r: CreditRequest, names: dict) -> dict:
    on = cc.today()
    st = "EXPIRED" if r.status == "APPROVED" and r.valid_until and r.valid_until < on else r.status
    return {"id": r.id, "kind": r.kind, "status": st, "branch_id": r.branch_id, "days": r.days, "amount": None if r.amount is None else str(r.amount), "one_time": r.one_time, "reason": r.reason,
            "requested_by": names.get(r.requested_by), "requested_at": r.requested_at.isoformat() if r.requested_at else None, "decided_by": names.get(r.decided_by),
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "valid_until": r.valid_until.isoformat() if r.valid_until else None, "remarks": r.remarks}


def _clean_branch_limits(db: Session, customer: Customer, limits: list, total: Decimal | None) -> list:
    out, seen, s = [], set(), ZERO
    for item in limits:
        try:
            bid, lim = int(item.get("branch_id")), _money(item.get("limit"), "Branch sub-limit")
        except (TypeError, ValueError):
            raise HTTPException(422, "Each branch limit needs a branch and an amount")
        if bid in seen:
            raise HTTPException(422, "A branch can appear only once in the sub-limits")
        if not db.scalar(select(OrgBranch.id).where(OrgBranch.id == bid, OrgBranch.customer_id == customer.id)):
            raise HTTPException(422, "Branch sub-limit refers to a branch of another organization")
        seen.add(bid)
        s += lim
        out.append({"branch_id": bid, "limit": str(lim)})
    if total is not None and s > total:
        raise HTTPException(422, f"Branch sub-limits ({s}) exceed the organization credit limit ({total})")
    return out


def _fac(db: Session, user: User, customer_id: int, fid: int, lock=False) -> tuple[Customer, CreditFacility]:
    c = _party(db, user, customer_id)
    q = select(CreditFacility).where(CreditFacility.id == fid, CreditFacility.customer_id == c.id)
    f = db.scalar(q.with_for_update() if lock else q)
    if not f:
        raise HTTPException(404, "Credit application not found")
    return c, f


@router.get("/api/customers/{customer_id}/credit")
def credit_summary(customer_id: int, branch_id: int | None = None, user: User = Depends(permission_required("credit.read")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    org = c.organization_id
    st = cc.status_for(db, org, c, branch_id)
    facs = db.scalars(select(CreditFacility).where(CreditFacility.customer_id == c.id).order_by(CreditFacility.id.desc())).all()
    reqs = db.scalars(select(CreditRequest).where(CreditRequest.customer_id == c.id).order_by(CreditRequest.id.desc())).all()
    events = db.scalars(select(CreditEvent).where(CreditEvent.customer_id == c.id).order_by(CreditEvent.id.desc()).limit(200)).all()
    names = _names(db, [f.requested_by for f in facs] + [f.reviewed_by for f in facs] + [f.approved_by for f in facs] + [r.requested_by for r in reqs] + [r.decided_by for r in reqs] + [e.user_id for e in events])
    ex = st.get("exposure") or cc.exposure(db, org, c)
    clean = {k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in st.items() if not k.startswith("_") and k != "exposure"}
    clean["exposure"] = {k: str(v) for k, v in ex.items()}
    return {"credit_customer": cc.is_credit_customer(c), "notify_days": cc.notify_days(c), "status": clean, "facilities": [_facility_out(f, names) for f in facs], "requests": [_request_out(r, names) for r in reqs],
            "events": [{"id": e.id, "action": e.action, "user": names.get(e.user_id), "at": e.created_at.isoformat(), "detail": e.detail} for e in events],
            "can_approve": has_permission(user, "credit.approve"), "can_override": has_permission(user, "credit.override") or has_permission(user, "credit.approve"), "can_request": has_permission(user, "credit.request")}


@router.get("/api/customers/{customer_id}/credit/check")
def credit_check(customer_id: int, amount: Decimal = Query(default=Decimal(0), ge=0), branch_id: int | None = None, user: User = Depends(permission_required("credit.read")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    st = cc.status_for(db, c.organization_id, c, branch_id, amount)
    blocked = False
    if st["applicable"] and st["state"] != "OK":
        try:
            cc.enforce(db, c.organization_id, c.id, "preview", user.id, branch_id, amount)
        except HTTPException:
            blocked = True
        db.rollback()
    return {"state": st["state"], "policy": st.get("policy"), "message": st["message"], "would_block": blocked, "available": str(st.get("available", "")), "limit": str(st.get("effective_limit", ""))}


@router.post("/api/customers/{customer_id}/credit/facilities", status_code=201)
def create_facility(customer_id: int, body: FacilityIn, user: User = Depends(permission_required("credit.request")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    if not cc.is_credit_customer(c):
        raise HTTPException(409, "Mark the organization as a Credit Customer (Yes) and save before applying for credit")
    if db.scalar(select(CreditFacility.id).where(CreditFacility.customer_id == c.id, CreditFacility.status.in_(("DRAFT", "SUBMITTED", "UNDER_REVIEW")))):
        raise HTTPException(409, "There is already an open credit application for this organization")
    prev = cc.latest_approved(db, c.id)
    if body.kind == "NEW" and prev:
        raise HTTPException(409, "A credit facility already exists. Use a revision or a renewal")
    if body.kind != "NEW" and not prev:
        raise HTTPException(409, "There is no approved facility to revise or renew")
    f = CreditFacility(organization_id=c.organization_id, customer_id=c.id, kind=body.kind, status="DRAFT", credit_days=body.credit_days, requested_limit=body.requested_limit, currency=body.currency.upper(),
                       agreement_no=body.agreement_no, agreement_file=body.agreement_file, reason=body.reason, requested_by=user.id, prev_id=prev.id if prev else None,
                       policy=prev.policy if prev else "SOFT", branch_limits=_clean_branch_limits(db, c, body.branch_limits, body.requested_limit if body.requested_limit > 0 else None))
    db.add(f)
    db.flush()
    cc.event(db, c.organization_id, c.id, "APPLICATION_CREATED", user.id, f.id, None, {"kind": body.kind, "requested_limit": str(body.requested_limit)})
    db.commit()
    return _facility_out(f, _names(db, [user.id]))


@router.patch("/api/customers/{customer_id}/credit/facilities/{fid}")
def edit_facility(customer_id: int, fid: int, body: FacilityPatch, user: User = Depends(permission_required("credit.request")), db: Session = Depends(get_db)):
    c, f = _fac(db, user, customer_id, fid, lock=True)
    if f.status != "DRAFT":
        raise HTTPException(409, "Only a draft application can be edited. Approved facilities change through a revision or renewal")
    changes = body.model_dump(exclude_unset=True)
    if "branch_limits" in changes:
        changes["branch_limits"] = _clean_branch_limits(db, c, changes["branch_limits"] or [], changes.get("requested_limit", f.requested_limit))
    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()
    for k, v in changes.items():
        setattr(f, k, v)
    cc.event(db, c.organization_id, c.id, "APPLICATION_EDITED", user.id, f.id, None, {k: str(v) for k, v in changes.items() if k != "branch_limits"})
    db.commit()
    return _facility_out(f, _names(db, [f.requested_by]))


@router.post("/api/customers/{customer_id}/credit/facilities/{fid}/submit")
def submit_facility(customer_id: int, fid: int, user: User = Depends(permission_required("credit.request")), db: Session = Depends(get_db)):
    c, f = _fac(db, user, customer_id, fid, lock=True)
    if f.status != "DRAFT":
        raise HTTPException(409, "Only a draft application can be submitted")
    if f.requested_limit <= 0:
        raise HTTPException(422, "Requested credit limit must be greater than zero")
    if not f.agreement_no:
        raise HTTPException(422, "Agreement number is required to submit a credit application")
    f.status, f.submitted_at = "SUBMITTED", datetime.now(timezone.utc)
    cc.event(db, c.organization_id, c.id, "APPLICATION_SUBMITTED", user.id, f.id)
    record_audit(db, "CREDIT_SUBMITTED", "CREDIT", user, c.organization_id, "CreditFacility", str(f.id), {"limit": str(f.requested_limit)})
    db.commit()
    return _facility_out(f, _names(db, [f.requested_by]))


@router.post("/api/customers/{customer_id}/credit/facilities/{fid}/review")
def review_facility(customer_id: int, fid: int, user: User = Depends(permission_required("credit.approve")), db: Session = Depends(get_db)):
    c, f = _fac(db, user, customer_id, fid, lock=True)
    if f.status != "SUBMITTED":
        raise HTTPException(409, "Only a submitted application can be put under review")
    f.status, f.reviewed_by = "UNDER_REVIEW", user.id
    cc.event(db, c.organization_id, c.id, "APPLICATION_UNDER_REVIEW", user.id, f.id)
    db.commit()
    return _facility_out(f, _names(db, [f.requested_by, f.reviewed_by]))


@router.post("/api/customers/{customer_id}/credit/facilities/{fid}/approve")
def approve_facility(customer_id: int, fid: int, body: DecisionIn, user: User = Depends(permission_required("credit.approve")), db: Session = Depends(get_db)):
    c, f = _fac(db, user, customer_id, fid, lock=True)
    if f.status not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(409, "Only a submitted application can be approved")
    if f.requested_by == user.id and not _demo(db, user):
        raise HTTPException(403, "You cannot approve your own credit application")
    limit = body.approved_limit if body.approved_limit is not None else f.requested_limit
    start = body.start_date or cc.today()
    if not body.expiry_date:
        raise HTTPException(422, "Credit expiry date is required")
    if body.expiry_date <= start:
        raise HTTPException(422, "Credit expiry date must be after the start date")
    f.branch_limits = _clean_branch_limits(db, c, body.branch_limits if body.branch_limits is not None else (f.branch_limits or []), limit)
    f.approved_limit, f.start_date, f.expiry_date = limit, start, body.expiry_date
    f.credit_days = body.credit_days if body.credit_days is not None else f.credit_days
    f.policy = body.policy or f.policy
    f.status, f.approved_by, f.approved_at, f.approval_remarks = "APPROVED", user.id, datetime.now(timezone.utc), body.remarks
    if f.prev_id and start <= cc.today():
        prev = db.get(CreditFacility, f.prev_id)
        if prev and prev.status == "APPROVED":
            prev.status = "SUPERSEDED"
    cc.event(db, c.organization_id, c.id, "APPLICATION_APPROVED", user.id, f.id, None, {"limit": str(limit), "start": start.isoformat(), "expiry": body.expiry_date.isoformat(), "policy": f.policy})
    record_audit(db, "CREDIT_APPROVED", "CREDIT", user, c.organization_id, "CreditFacility", str(f.id), {"limit": str(limit), "expiry": body.expiry_date.isoformat()})
    db.commit()
    return _facility_out(f, _names(db, [f.requested_by, f.approved_by]))


@router.post("/api/customers/{customer_id}/credit/facilities/{fid}/reject")
def reject_facility(customer_id: int, fid: int, body: DecisionIn, user: User = Depends(permission_required("credit.approve")), db: Session = Depends(get_db)):
    c, f = _fac(db, user, customer_id, fid, lock=True)
    if f.status not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(409, "Only a submitted application can be rejected")
    if not (body.remarks or "").strip():
        raise HTTPException(422, "Give a reason for rejecting")
    f.status, f.approved_by, f.approved_at, f.approval_remarks = "REJECTED", user.id, datetime.now(timezone.utc), body.remarks
    cc.event(db, c.organization_id, c.id, "APPLICATION_REJECTED", user.id, f.id, None, {"remarks": body.remarks})
    record_audit(db, "CREDIT_REJECTED", "CREDIT", user, c.organization_id, "CreditFacility", str(f.id), {})
    db.commit()
    return _facility_out(f, _names(db, [f.requested_by, f.approved_by]))


# ---------------------------------------------------------------- grace / extension / override / release
class RequestIn(BaseModel):
    kind: str = Field(pattern="^(GRACE|EXTENSION|OVERRIDE|RELEASE)$")
    branch_id: int | None = None
    days: int | None = Field(default=None, ge=1, le=180)
    amount: Decimal | None = Field(default=None, ge=0)
    one_time: bool = False
    valid_until: date | None = None
    reason: str = Field(min_length=3, max_length=2000)


class RequestDecision(BaseModel):
    valid_until: date | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)


@router.post("/api/customers/{customer_id}/credit/requests", status_code=201)
def create_request(customer_id: int, body: RequestIn, user: User = Depends(permission_required("credit.request")), db: Session = Depends(get_db)):
    c = _party(db, user, customer_id)
    fac = cc.active_facility(db, c.id) or cc.latest_approved(db, c.id)
    if not fac:
        raise HTTPException(409, "There is no approved credit facility for this organization")
    _check_branch(db, c, body.branch_id)
    if body.kind == "GRACE" and not body.days:
        raise HTTPException(422, "Requested grace days are required")
    if body.kind == "EXTENSION" and (not body.amount or body.amount <= 0):
        raise HTTPException(422, "Requested additional amount is required")
    if body.kind == "OVERRIDE" and fac.policy != "SOFT":
        raise HTTPException(409, "Overrides apply only to soft-blocked customers. A hard block needs a formal release or renewal")
    if body.kind == "RELEASE" and fac.policy != "HARD":
        raise HTTPException(409, "A release applies only to hard-blocked customers")
    r = CreditRequest(organization_id=c.organization_id, customer_id=c.id, facility_id=fac.id, branch_id=body.branch_id, kind=body.kind, days=body.days, amount=body.amount, one_time=body.one_time,
                      valid_until=body.valid_until, reason=body.reason, requested_by=user.id)
    db.add(r)
    db.flush()
    cc.event(db, c.organization_id, c.id, f"{body.kind}_REQUESTED", user.id, fac.id, r.id, {"reason": body.reason, "days": body.days, "amount": str(body.amount) if body.amount is not None else None})
    db.commit()
    return _request_out(r, _names(db, [user.id]))


def _decide(db: Session, user: User, customer_id: int, rid: int, approve: bool, body: RequestDecision) -> dict:
    c = _party(db, user, customer_id)
    r = db.scalar(select(CreditRequest).where(CreditRequest.id == rid, CreditRequest.customer_id == c.id).with_for_update())
    if not r:
        raise HTTPException(404, "Request not found")
    if r.status != "PENDING":
        raise HTTPException(409, "This request has already been decided")
    allowed = has_permission(user, "credit.approve") or (r.kind in ("OVERRIDE",) and has_permission(user, "credit.override"))
    if not allowed:
        raise HTTPException(403, "You are not authorised to decide this request")
    if r.requested_by == user.id and not _demo(db, user):
        raise HTTPException(403, "You cannot approve your own request")
    r.decided_by, r.decided_at, r.remarks = user.id, datetime.now(timezone.utc), body.remarks
    if not approve:
        if not (body.remarks or "").strip():
            raise HTTPException(422, "Give a reason for rejecting")
        r.status = "REJECTED"
    else:
        r.status = "APPROVED"
        if body.amount is not None:
            r.amount = body.amount
        if r.kind == "GRACE":
            r.valid_until = body.valid_until or (cc.today() + timedelta(days=r.days or 0))
        elif body.valid_until:
            r.valid_until = body.valid_until
        if r.kind in ("OVERRIDE", "RELEASE") and not r.valid_until and not r.one_time:
            raise HTTPException(422, "Set how long the approval is valid, or make it one-time")
        if r.valid_until and r.valid_until < cc.today():
            raise HTTPException(422, "Valid-until date cannot be in the past")
    cc.event(db, c.organization_id, c.id, f"{r.kind}_{r.status}", user.id, r.facility_id, r.id, {"remarks": body.remarks, "valid_until": r.valid_until.isoformat() if r.valid_until else None, "amount": str(r.amount) if r.amount is not None else None})
    record_audit(db, f"CREDIT_{r.kind}_{r.status}", "CREDIT", user, c.organization_id, "CreditRequest", str(r.id), {})
    db.commit()
    return _request_out(r, _names(db, [r.requested_by, r.decided_by]))


@router.post("/api/customers/{customer_id}/credit/requests/{rid}/approve")
def approve_request(customer_id: int, rid: int, body: RequestDecision, user: User = Depends(permission_required("credit.read")), db: Session = Depends(get_db)):
    return _decide(db, user, customer_id, rid, True, body)


@router.post("/api/customers/{customer_id}/credit/requests/{rid}/reject")
def reject_request(customer_id: int, rid: int, body: RequestDecision, user: User = Depends(permission_required("credit.read")), db: Session = Depends(get_db)):
    return _decide(db, user, customer_id, rid, False, body)


# ---------------------------------------------------------------- reports / alerts
@router.get("/api/credit/overview")
def overview(user: User = Depends(permission_required("credit.read")), db: Session = Depends(get_db)):
    org = _tenant(user)
    rows = []
    for c in db.scalars(select(Customer).where(Customer.organization_id == org).order_by(Customer.name)).all():
        if not cc.is_credit_customer(c):
            continue
        st = cc.status_for(db, org, c)
        ex = st.get("exposure") or {}
        rows.append({"customer_id": c.id, "customer": c.name, "state": st["state"], "policy": st.get("policy"), "limit": str(st.get("effective_limit", "")), "exposure": str(ex.get("exposure", "")), "overdue": str(ex.get("overdue", "")),
                     "available": str(st.get("available", "")), "expiry_date": str(st["expiry_date"]) if st.get("expiry_date") else None})
    return {"customers": rows, "alerts": cc.expiry_alerts(db, org)}
