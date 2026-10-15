from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session, selectinload

from . import config
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number
from .db import get_db
from .schemas_auth import _clean_party_data
from .models import Customer, JobCharge, MasterRecord, Organization, Quotation, QuotationEvent, QuotationLine, Shipment, User
from .quote_calc import calc_line, charge_margin_pct, cost_sheet, q2

ZERO_AMT = __import__("decimal").Decimal(0)
router = APIRouter(prefix="/api/quotations", tags=["quotations"])
MODES = "^(Sea|Air|Road|Rail|Multimodal|Courier)$"
DIRECTIONS = "^(Export|Import|Domestic|Cross-Trade)$"
LIST_STATUSES = "^(DRAFT|SENT|ACCEPTED|REJECTED|CONVERTED|EXPIRED|SUPERSEDED|CANCELLED|LOST)$"
EDITABLE_APPROVALS = ("DRAFT", "REJECTED")
ADMIN_EDITABLE_STATUSES = ("DRAFT", "SENT", "ACCEPTED", "REJECTED")  # an administrator may still correct these; converted, cancelled, lost and superseded quotations are final


def _can_edit(user: User, q: Quotation) -> bool:
    if q.status == "DRAFT" and q.approval_status in EDITABLE_APPROVALS:
        return True
    return q.status in ADMIN_EDITABLE_STATUSES and has_permission(user, "quote.edit_locked")


class Breakup(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    qty: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    rate: Decimal = Field(ge=0, max_digits=16, decimal_places=4)


class QLine(BaseModel):
    side: str = Field(default="SELL", pattern="^(SELL|BUY)$")
    charge_name: str = Field(default="", max_length=160)
    carrier: str = Field(default="", max_length=160)
    basis: str = Field(default="Per Shipment", max_length=40)
    container_type: str | None = Field(default=None, max_length=40)
    container_breakup: list[Breakup] | None = Field(default=None, max_length=30)
    ccy: str = Field(default="INR", pattern="^[A-Z]{3}$")
    exch: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=6)
    rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    qty: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=3)
    taxable: bool | None = None
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=2)
    remarks: str = Field(default="", max_length=500)


class QuotationIn(BaseModel):
    quote_date: date | None = None
    valid_until: date | None = None
    customer_id: int | None = None
    party_name: str = Field(default="", max_length=200)
    mode: str = Field(default="Sea", pattern=MODES)
    direction: str = Field(default="Export", pattern=DIRECTIONS)
    shipment_type: str | None = Field(default=None, max_length=40)
    quote_type: str | None = Field(default=None, max_length=60)
    carrier_mode: str = Field(default="single", pattern="^(single|multi)$")
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    terms: str | None = Field(default=None, max_length=8000)
    remarks: str | None = Field(default=None, max_length=2000)
    header: dict[str, Any] = Field(default_factory=dict)
    lines: list[QLine] = Field(default_factory=list, max_length=400)

    @field_validator("header")
    @classmethod
    def _header(cls, value):
        return None if value is None else _clean_party_data(value)


class QuotationPatch(BaseModel):
    quote_date: date | None = None
    valid_until: date | None = None
    customer_id: int | None = None
    party_name: str | None = Field(default=None, max_length=200)
    mode: str | None = Field(default=None, pattern=MODES)
    direction: str | None = Field(default=None, pattern=DIRECTIONS)
    shipment_type: str | None = Field(default=None, max_length=40)
    quote_type: str | None = Field(default=None, max_length=60)
    carrier_mode: str | None = Field(default=None, pattern="^(single|multi)$")
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    terms: str | None = Field(default=None, max_length=8000)
    remarks: str | None = Field(default=None, max_length=2000)
    header: dict[str, Any] | None = None
    lines: list[QLine] | None = Field(default=None, max_length=400)

    @field_validator("header")
    @classmethod
    def _header(cls, value):
        return None if value is None else _clean_party_data(value)


class Decision(BaseModel):
    remarks: str | None = Field(default=None, max_length=1000)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quotations are tenant-scoped; Super Admin does not manage them")
    return user.organization_id


def effective_status(q: Quotation) -> str:
    if q.status == "SENT" and q.valid_until and q.valid_until < date.today():
        return "EXPIRED"
    return q.status


def _names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.first_name + " " + (u.last_name or "")).strip() or u.username or u.email for u in db.scalars(select(User).where(User.id.in_(ids)))}


def _line_out(l: QuotationLine, margins: dict[str, Decimal | None]) -> dict:
    m = margins.get((l.charge_name or "").strip().lower())
    return {"line_no": l.line_no, "side": l.side, "charge_name": l.charge_name, "carrier": l.carrier, "basis": l.basis, "container_type": l.container_type, "container_breakup": l.container_breakup or [],
            "ccy": l.ccy, "exch": str(l.exch), "rate": str(l.rate), "qty": str(l.qty), "taxable": bool(l.taxable), "tax_pct": str(l.tax_pct), "remarks": l.remarks or "",
            "amount_fcy": str(l.amount_fcy), "amount": str(l.amount), "gst_amount": str(l.gst_amount), "total_inr": str(l.total_inr), "margin_rate": None if m is None else str(m)}


def _out(db: Session, q: Quotation, user: User, with_lines: bool = False, names: dict | None = None) -> dict:
    can_cost = has_permission(user, "quote.view_cost")
    sell = [l for l in q.lines if l.side == "SELL"]
    by_carrier: dict[str, Decimal] = {}
    for l in sell:
        by_carrier[l.carrier or "—"] = by_carrier.get(l.carrier or "—", Decimal(0)) + l.amount
    quoted = min(by_carrier.values()) if by_carrier else Decimal(0)
    names = names if names is not None else _names(db, {q.prepared_by, q.approved_by})
    data = {"id": q.id, "number": q.number, "status": q.status, "effective_status": effective_status(q), "approval_status": q.approval_status, "quote_date": q.quote_date, "valid_until": q.valid_until,
            "customer_id": q.customer_id, "party_name": q.party_name, "mode": q.mode, "direction": q.direction, "shipment_type": q.shipment_type, "quote_type": q.quote_type, "carrier_mode": q.carrier_mode,
            "pol": q.pol, "pod": q.pod, "terms": q.terms, "remarks": q.remarks, "header": q.header or {}, "job_number": q.job_number, "quoted_amount": str(quoted), "carrier_totals": {k: str(v) for k, v in by_carrier.items()},
            "sent_at": q.sent_at, "responded_at": q.responded_at, "created_at": q.created_at, "root_id": q.root_id or q.id, "revision_no": q.revision_no,
            "prepared_by": q.prepared_by, "prepared_by_name": names.get(q.prepared_by), "prepared_at": q.prepared_at, "submitted_at": q.submitted_at,
            "approved_by": q.approved_by, "approved_by_name": names.get(q.approved_by), "approved_at": q.approved_at, "approval_remarks": q.approval_remarks,
            "can_view_cost": can_cost, "editable": _can_edit(user, q), "closure_reason": (q.header or {}).get("closure_reason", "")}
    if with_lines:
        visible = q.lines if can_cost else sell
        margins = charge_margin_pct(list(q.lines)) if can_cost else {}
        data["lines"] = [_line_out(l, margins) for l in visible]
        if can_cost:
            data["cost_sheet"] = cost_sheet(list(q.lines))
    return data


def _event(db: Session, q: Quotation, action: str, from_state: str | None, to_state: str | None, user: User, remarks: str | None = None) -> None:
    db.add(QuotationEvent(quotation_id=q.id, action=action, from_state=from_state, to_state=to_state, user_id=user.id, remarks=remarks))


def _currency_rates(db: Session, org_id: int) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "currency", MasterRecord.status == "ACTIVE")):
        raw = (r.data or {}).get("exchange_rate")
        try:
            value = Decimal(str(raw).replace(",", "").strip())
        except Exception:
            continue
        if value > 0:
            rates[r.code.upper()] = value
    return rates


def _apply_lines(db: Session, q: Quotation, lines: list[QLine], user: User, org_id: int) -> None:
    rates = _currency_rates(db, org_id)
    can_override = has_permission(user, "quote.rate_override")
    previous = {(l.ccy, l.exch) for l in q.lines}
    for line in lines:
        if line.ccy != "INR" and line.exch <= 0 and not line.container_breakup is None and False:
            pass
        if line.ccy != "INR" and line.exch <= 0:
            raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR lines")
        master = rates.get(line.ccy)
        if line.ccy != "INR" and master is not None and line.exch != master and (line.ccy, line.exch) not in previous and not can_override:
            raise HTTPException(status_code=403, detail=f"Exchange rate for {line.ccy} is {master} in the Currency Master; changing it needs the quote.rate_override permission")
    q.lines.clear()
    session = object_session(q)
    if session is not None:
        session.flush()
    for i, line in enumerate(lines, start=1):
        taxable = line.taxable if line.taxable is not None else line.tax_pct > 0
        breakup = [{"type": b.type, "qty": str(b.qty), "rate": str(b.rate)} for b in line.container_breakup] if line.container_breakup else None
        c = calc_line(line.qty, line.rate, line.ccy, line.exch, taxable, line.tax_pct, breakup)
        q.lines.append(QuotationLine(line_no=i, side=line.side, charge_name=line.charge_name.strip(), carrier=line.carrier.strip(), basis=line.basis, container_type=line.container_type, container_breakup=breakup,
                                     ccy=line.ccy, exch=c["exch"], rate=c["unit_rate"], qty=c["units"], taxable=taxable, tax_pct=c["tax_pct"], remarks=line.remarks.strip(), amount_fcy=c["amount_fcy"],
                                     amount=c["amount_inr"], gst_amount=c["gst"], total_inr=c["total_inr"]))


def _load(db: Session, org_id: int, quote_id: int, lock: bool = False) -> Quotation:
    query = select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.id == quote_id, Quotation.organization_id == org_id)
    if lock:
        query = query.with_for_update()
    q = db.scalar(query)
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return q


def _check_customer(db: Session, org_id: int, customer_id: int | None) -> None:
    if customer_id and not db.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.organization_id == org_id)):
        raise HTTPException(status_code=404, detail="Customer not found")


@router.get("")
def list_quotations(status_filter: str | None = Query(default=None, alias="status", pattern=LIST_STATUSES), q: str | None = Query(default=None, max_length=100),
                    limit: int = Query(default=300, ge=1, le=1000), user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    query = select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.organization_id == org_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Quotation.number.ilike(like), Quotation.party_name.ilike(like), Quotation.pol.ilike(like), Quotation.pod.ilike(like)))
    quotes = list(db.scalars(query.order_by(Quotation.quote_date.desc(), Quotation.id.desc()).limit(limit)))
    names = _names(db, {x.prepared_by for x in quotes} | {x.approved_by for x in quotes})
    rows = [_out(db, x, user, names=names) for x in quotes]
    if status_filter:
        rows = [r for r in rows if r["effective_status"] == status_filter]
    return rows


@router.post("", status_code=201)
def create_quotation(payload: QuotationIn, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    _check_customer(db, org_id, payload.customer_id)
    today = date.today()
    quote = Quotation(organization_id=org_id, status="DRAFT", approval_status="DRAFT", quote_date=payload.quote_date or today, valid_until=payload.valid_until or today + timedelta(days=15), created_by=user.id,
                      prepared_by=user.id, prepared_at=datetime.now(timezone.utc), **payload.model_dump(exclude={"lines", "quote_date", "valid_until"}))
    _apply_lines(db, quote, payload.lines, user, org_id)
    db.add(quote)
    db.flush()
    quote.root_id = quote.id
    _event(db, quote, "CREATED", None, "DRAFT", user)
    record_audit(db, "QUOTATION_CREATED", "SALES", user, org_id, "Quotation", str(quote.id), {"party": quote.party_name})
    db.commit()
    return _out(db, _load(db, org_id, quote.id), user, True)


@router.get("/{quote_id}")
def get_quotation(quote_id: int, user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    return _out(db, _load(db, _tenant(user), quote_id), user, True)


@router.patch("/{quote_id}")
def update_quotation(quote_id: int, payload: QuotationPatch, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if not _can_edit(user, quote):
        raise HTTPException(status_code=409, detail="This quotation is locked (it is converted, cancelled or lost, or you may not edit it after approval). Use Revise to create a new revision.")
    admin_edit = not (quote.status == "DRAFT" and quote.approval_status in EDITABLE_APPROVALS)
    changes = payload.model_dump(exclude_unset=True)
    if "customer_id" in changes:
        _check_customer(db, org_id, changes["customer_id"])
    for key, value in changes.items():
        if key == "lines" or (value is None and key not in ("customer_id", "valid_until", "terms", "remarks", "shipment_type", "quote_type", "pol", "pod")):
            continue
        setattr(quote, key, value)
    if payload.lines is not None:
        _apply_lines(db, quote, payload.lines, user, org_id)
    record_audit(db, "QUOTATION_UPDATED", "SALES", user, org_id, "Quotation", str(quote.id), {"fields": sorted(k for k in changes if k != "lines"), "admin_edit": admin_edit})
    if admin_edit:
        _event(db, quote, "ADMIN_EDITED", quote.status, quote.status, user, "edited by an administrator after " + (quote.approval_status or "approval").lower() + " (" + (quote.number or "no number") + ")")
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


@router.delete("/{quote_id}", status_code=204)
def delete_draft(quote_id: int, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT" or quote.approval_status not in EDITABLE_APPROVALS or quote.revision_no > 0:
        raise HTTPException(status_code=409, detail="Only unsubmitted first-revision draft quotations can be deleted")
    record_audit(db, "QUOTATION_DRAFT_DELETED", "SALES", user, org_id, "Quotation", str(quote.id), {"party": quote.party_name})
    db.delete(quote)
    db.commit()


# ---------------------------------------------------------------- approval workflow (internal)
@router.post("/{quote_id}/approval/submit")
def submit_for_approval(quote_id: int, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    """DRAFT (or REJECTED) -> SUBMITTED. The submitter becomes the 'prepared by' user."""
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT" or quote.approval_status not in EDITABLE_APPROVALS:
        raise HTTPException(status_code=409, detail="Only a draft or rejected quotation can be submitted for approval")
    if not quote.party_name:
        raise HTTPException(status_code=422, detail="Client is required before submitting")
    sell = [l for l in quote.lines if l.side == "SELL" and l.rate > 0]
    if not sell:
        raise HTTPException(status_code=422, detail="Add at least one sell charge with a rate before submitting")
    if quote.valid_until and quote.valid_until < date.today():
        raise HTTPException(status_code=422, detail="The validity date has already passed")
    before = quote.approval_status
    quote.approval_status = "SUBMITTED"
    quote.prepared_by, quote.prepared_at, quote.submitted_at = user.id, datetime.now(timezone.utc), datetime.now(timezone.utc)
    quote.approved_by, quote.approved_at = None, None
    _event(db, quote, "SUBMITTED", before, "SUBMITTED", user, "awaiting approval")  # the official number is generated only when the quotation is approved
    record_audit(db, "QUOTATION_SUBMITTED", "SALES", user, org_id, "Quotation", str(quote.id), {})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


def _sod_blocked(db: Session, user: User, quote: Quotation) -> bool:
    org = db.get(Organization, quote.organization_id)
    if not (config.QUOTE_SOD and not (org and org.is_demo) and quote.prepared_by == user.id):
        return False
    # segregation of duties applies only while another active user can approve; a one-person company must still be able to issue quotations
    approvers = [u for u in db.scalars(select(User).where(User.organization_id == quote.organization_id, User.status == "ACTIVE", User.id != user.id))
                 if u.is_super_admin or any(p.code == "quote.approve" for r in u.roles for p in r.permissions)]
    return bool(approvers)


def _assign_number(db: Session, org_id: int, quote: Quotation, user: User) -> None:
    """The official quotation number (from Settings -> Sequence Setting) is generated when the quotation is approved, once, and kept through later revisions (-R1, -R2)."""
    if quote.number:
        return
    root = db.get(Quotation, quote.root_id) if quote.root_id and quote.root_id != quote.id else None
    if quote.revision_no > 0 and root is not None and root.number:
        quote.number = f"{root.number}-R{quote.revision_no}"
    else:
        quote.number = next_number(db, org_id, "quotation", "QT", quote.quote_date, user=user)


@router.post("/{quote_id}/approval/approve")
def approve(quote_id: int, payload: Decision | None = None, user: User = Depends(permission_required("quote.approve")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.approval_status != "SUBMITTED":
        raise HTTPException(status_code=409, detail="Only a submitted quotation can be approved")
    if _sod_blocked(db, user, quote):
        raise HTTPException(status_code=403, detail="Segregation of duties: you prepared this quotation, so someone else must approve it")
    quote.approval_status = "APPROVED"
    quote.approved_by, quote.approved_at = user.id, datetime.now(timezone.utc)
    quote.approval_remarks = (payload.remarks if payload else None) or None
    _assign_number(db, org_id, quote, user)  # the official number comes from the active sequence the moment the quotation is approved
    _event(db, quote, "APPROVED", "SUBMITTED", "APPROVED", user, f"{quote.number}" + (f" · {quote.approval_remarks}" if quote.approval_remarks else ""))
    record_audit(db, "QUOTATION_APPROVED", "SALES", user, org_id, "Quotation", str(quote.id), {})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


@router.post("/{quote_id}/approval/reject")
def reject_approval(quote_id: int, payload: Decision, user: User = Depends(permission_required("quote.approve")), db: Session = Depends(get_db)):
    """SUBMITTED -> REJECTED (reason required). The quotation returns to the preparer for correction and resubmission."""
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.approval_status != "SUBMITTED":
        raise HTTPException(status_code=409, detail="Only a submitted quotation can be rejected")
    if not payload.remarks or len(payload.remarks.strip()) < 3:
        raise HTTPException(status_code=422, detail="A reason is required when rejecting a quotation")
    if _sod_blocked(db, user, quote):
        raise HTTPException(status_code=403, detail="Segregation of duties: you prepared this quotation, so someone else must review it")
    quote.approval_status = "REJECTED"
    quote.approved_by, quote.approved_at = None, None
    quote.approval_remarks = payload.remarks.strip()
    _event(db, quote, "REJECTED", "SUBMITTED", "REJECTED", user, quote.approval_remarks)
    record_audit(db, "QUOTATION_APPROVAL_REJECTED", "SALES", user, org_id, "Quotation", str(quote.id), {"reason": quote.approval_remarks})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


# ---------------------------------------------------------------- revisions and audit trail
@router.post("/{quote_id}/revise", status_code=201)
def revise(quote_id: int, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    """Create a new DRAFT revision of an approved/issued quotation (the earlier revision becomes SUPERSEDED)."""
    org_id = _tenant(user)
    src = _load(db, org_id, quote_id, lock=True)
    eff = effective_status(src)
    revisable = (src.status == "DRAFT" and src.approval_status == "APPROVED") or eff in ("SENT", "REJECTED", "EXPIRED", "LOST")
    if not revisable:
        raise HTTPException(status_code=409, detail="Only an approved, issued, rejected or expired quotation can be revised (drafts are edited directly; accepted/converted quotations are final)")
    root = src.root_id or src.id
    latest = db.scalar(select(func.max(Quotation.revision_no)).where(Quotation.organization_id == org_id, Quotation.root_id == root)) or 0
    if src.revision_no != latest:
        raise HTTPException(status_code=409, detail="A newer revision already exists; revise the latest one")
    copy = Quotation(organization_id=org_id, status="DRAFT", approval_status="DRAFT", quote_date=date.today(), valid_until=date.today() + timedelta(days=15), customer_id=src.customer_id, party_name=src.party_name,
                     mode=src.mode, direction=src.direction, shipment_type=src.shipment_type, quote_type=src.quote_type, carrier_mode=src.carrier_mode, pol=src.pol, pod=src.pod, terms=src.terms, remarks=src.remarks,
                     header=dict(src.header or {}), created_by=user.id, prepared_by=user.id, prepared_at=datetime.now(timezone.utc), root_id=root, revision_no=latest + 1)
    for l in src.lines:
        copy.lines.append(QuotationLine(line_no=l.line_no, side=l.side, charge_name=l.charge_name, carrier=l.carrier, basis=l.basis, container_type=l.container_type, container_breakup=l.container_breakup, ccy=l.ccy, exch=l.exch,
                                        rate=l.rate, qty=l.qty, taxable=l.taxable, tax_pct=l.tax_pct, remarks=l.remarks, amount_fcy=l.amount_fcy, amount=l.amount, gst_amount=l.gst_amount, total_inr=l.total_inr))
    db.add(copy)
    db.flush()
    if src.status in ("DRAFT", "SENT"):
        _event(db, src, "SUPERSEDED", src.status, "SUPERSEDED", user, f"Replaced by revision {copy.revision_no}")
        src.status = "SUPERSEDED"
    _event(db, copy, "REVISED", None, "DRAFT", user, f"Revision {copy.revision_no} of {src.number or 'draft #' + str(src.id)}")
    record_audit(db, "QUOTATION_REVISED", "SALES", user, org_id, "Quotation", str(copy.id), {"from": src.id, "revision": copy.revision_no})
    db.commit()
    return _out(db, _load(db, org_id, copy.id), user, True)


@router.get("/{quote_id}/revisions")
def revisions(quote_id: int, user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    q = _load(db, org_id, quote_id)
    chain = list(db.scalars(select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.organization_id == org_id, Quotation.root_id == (q.root_id or q.id)).order_by(Quotation.revision_no)))
    names = _names(db, {x.prepared_by for x in chain} | {x.approved_by for x in chain})
    return [{**{k: v for k, v in _out(db, x, user, names=names).items() if k in ("id", "number", "revision_no", "status", "effective_status", "approval_status", "party_name", "quote_date", "quoted_amount", "prepared_by_name", "approved_by_name", "job_number")}, "current": x.id == q.id} for x in chain]


@router.get("/{quote_id}/events")
def events(quote_id: int, user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    q = _load(db, org_id, quote_id)
    chain_ids = [i for i in db.scalars(select(Quotation.id).where(Quotation.organization_id == org_id, Quotation.root_id == (q.root_id or q.id)))]
    rows = list(db.scalars(select(QuotationEvent).where(QuotationEvent.quotation_id.in_(chain_ids)).order_by(QuotationEvent.created_at, QuotationEvent.id)))
    names = _names(db, {e.user_id for e in rows})
    rev = {x.id: x.revision_no for x in db.scalars(select(Quotation).where(Quotation.id.in_(chain_ids)))}
    return [{"id": e.id, "quotation_id": e.quotation_id, "revision_no": rev.get(e.quotation_id, 0), "action": e.action, "from_state": e.from_state, "to_state": e.to_state, "user": names.get(e.user_id), "remarks": e.remarks, "at": e.created_at} for e in rows]


# ---------------------------------------------------------------- issue to client and client response
@router.post("/{quote_id}/send")
def send_quotation(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """APPROVED draft -> SENT: assigns the quotation number and locks the quotation."""
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft quotations can be sent")
    if quote.approval_status != "APPROVED":
        raise HTTPException(status_code=409, detail="The quotation must be approved before it can be issued to the client")
    if not quote.party_name:
        raise HTTPException(status_code=422, detail="Client is required before sending")
    if not any(l.rate > 0 for l in quote.lines if l.side == "SELL"):
        raise HTTPException(status_code=422, detail="Add at least one sell charge with a rate before sending")
    if quote.valid_until and quote.valid_until < date.today():
        raise HTTPException(status_code=422, detail="The validity date has already passed")
    if not quote.number:  # quotations approved before numbering moved to approval
        _assign_number(db, org_id, quote, user)
    quote.status = "SENT"
    quote.sent_at = datetime.now(timezone.utc)
    _event(db, quote, "SENT", "DRAFT", "SENT", user, quote.number)
    record_audit(db, "QUOTATION_SENT", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


def _respond(quote_id: int, outcome: str, user: User, db: Session) -> dict:
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if effective_status(quote) != "SENT":
        raise HTTPException(status_code=409, detail="Only a sent, unexpired quotation can be accepted or rejected")
    quote.status = outcome
    quote.responded_at = datetime.now(timezone.utc)
    _event(db, quote, "CLIENT_" + outcome, "SENT", outcome, user)
    record_audit(db, f"QUOTATION_{outcome}", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


@router.post("/{quote_id}/accept")
def accept(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    return _respond(quote_id, "ACCEPTED", user, db)


class Closure(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _close(quote_id: int, outcome: str, label: str, body: Closure, user: User, db: Session) -> dict:
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status in ("CONVERTED", "SUPERSEDED", "CANCELLED", "LOST"):
        raise HTTPException(status_code=409, detail="This quotation is already " + quote.status.lower() + " and cannot be changed")
    if outcome == "LOST" and quote.status == "DRAFT":
        raise HTTPException(status_code=409, detail="A quotation that was never issued cannot be marked lost; cancel it instead")
    before = quote.status
    quote.status = outcome
    quote.responded_at = datetime.now(timezone.utc)
    quote.header = {**(quote.header or {}), "closure_reason": body.reason.strip(), "closed_from": before}
    _event(db, quote, outcome, before, outcome, user, body.reason.strip())
    record_audit(db, f"QUOTATION_{outcome}", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number, "reason": body.reason.strip(), "from": before})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)


@router.post("/{quote_id}/cancel")
def cancel_quotation(quote_id: int, body: Closure, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """We withdraw the quotation (wrong rates, customer withdrew the enquiry, duplicate ...)."""
    return _close(quote_id, "CANCELLED", "cancelled", body, user, db)


@router.post("/{quote_id}/lost")
def mark_lost(quote_id: int, body: Closure, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """The customer chose another forwarder / dropped the shipment: closed as LOST with the reason, for win-loss reporting."""
    return _close(quote_id, "LOST", "lost", body, user, db)


@router.post("/{quote_id}/reject")
def reject(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    return _respond(quote_id, "REJECTED", user, db)


def _copy_quote_to_job(db: Session, org_id: int, quote: Quotation, job: Shipment, sell: list, buy: list) -> None:
    """Sell charges become the job's sell side; matching buy costs its buy side."""
    rows: list[JobCharge] = []
    for l in sell:
        detail = "; ".join(f"{b['type']} x{b['qty']} @ {b['rate']}" for b in (l.container_breakup or []))
        rows.append(JobCharge(organization_id=org_id, shipment_id=job.id, charge_name=l.charge_name, description=(f"From quotation {quote.number}" + (f" ({detail})" if detail else ""))[:240], uom=l.basis, qty=l.qty, hsn="",
                              sell_ccy=l.ccy, sell_exch=l.exch, sell_rate=l.rate, sell_tax_type="Taxable" if l.taxable else "Non-Taxable", sell_tax_pct=l.tax_pct if l.taxable else Decimal(0), client=quote.party_name, use_client=True))
    for b in buy:
        match = next((r for r in rows if r.charge_name.strip().lower() == b.charge_name.strip().lower() and not r.buy_rate), None)
        base_rate = b.rate if (match is None or match.qty == b.qty or not match.qty) else (b.amount_fcy / match.qty).quantize(Decimal("0.0001"))
        fields = dict(buy_ccy=b.ccy, buy_exch=b.exch, buy_rate=base_rate, buy_tax_type="Taxable" if b.taxable else "Non-Taxable", buy_tax_pct=b.tax_pct if b.taxable else Decimal(0))
        if match is not None:
            for k, v in fields.items():
                setattr(match, k, v)
        else:
            rows.append(JobCharge(organization_id=org_id, shipment_id=job.id, charge_name=b.charge_name, description=f"Buy cost from quotation {quote.number}"[:240], uom=b.basis, qty=b.qty, hsn="", sell_rate=Decimal(0), **fields))
    for i, r in enumerate(rows, start=1):
        r.line_no = i
        db.add(r)
    job.revenue = sum((l.amount for l in sell), Decimal(0))


def select_lines_for_carrier(quote: Quotation, carrier: str | None) -> tuple[list, list]:
    sell = [l for l in quote.lines if l.side == "SELL" and l.rate > 0]
    buy = [l for l in quote.lines if l.side == "BUY" and l.rate > 0]
    carriers = sorted({l.carrier for l in sell if l.carrier})
    if len(carriers) > 1 and carrier in carriers:
        sell = [l for l in sell if l.carrier == carrier]
        buy = [l for l in buy if not l.carrier or l.carrier == carrier]
    return sell, buy


def link_booking_job(db: Session, org_id: int, quote_id: int, job: Shipment, user: User, carrier: str | None) -> Quotation:
    """A confirmed booking that came from a quotation carries the quotation's charges to the job and closes the quotation as CONVERTED."""
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "ACCEPTED":
        raise HTTPException(status_code=409, detail="The linked quotation " + (quote.number or "") + " is " + quote.status + "; only an accepted quotation can be booked")
    sell, buy = select_lines_for_carrier(quote, carrier)
    _copy_quote_to_job(db, org_id, quote, job, sell, buy)
    quote.status = "CONVERTED"
    quote.job_number = job.job_number
    _event(db, quote, "CONVERTED", "ACCEPTED", "CONVERTED", user, job.job_number)
    record_audit(db, "QUOTATION_CONVERTED", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number, "job_number": job.job_number, "via": "booking"})
    return quote


@router.post("/{quote_id}/convert")
def convert_to_job(quote_id: int, carrier: str | None = Query(default=None, max_length=160), user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """ACCEPTED -> CONVERTED: creates a job; sell charges become the job's sell side and matching buy costs its buy side."""
    org_id = _tenant(user)
    if not has_permission(user, "shipment.create"):
        raise HTTPException(status_code=403, detail="Permission denied: creating the job needs shipment.create")
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "ACCEPTED":
        raise HTTPException(status_code=409, detail="Only an accepted quotation can be converted to a job")
    sell = [l for l in quote.lines if l.side == "SELL" and l.rate > 0]
    buy = [l for l in quote.lines if l.side == "BUY" and l.rate > 0]
    carriers = sorted({l.carrier for l in sell if l.carrier})
    if len(carriers) > 1:
        if not carrier or carrier not in carriers:
            raise HTTPException(status_code=422, detail="This quotation compares several carriers (" + ", ".join(carriers) + "); choose which carrier's rates to convert")
        sell = [l for l in sell if l.carrier == carrier]
        buy = [l for l in buy if not l.carrier or l.carrier == carrier]
    from .shipments_api import _next_job_number  # local import: shipments_api imports auth helpers too

    from .credit_control import enforce as credit_enforce

    credit_enforce(db, org_id, quote.customer_id, "convert quotation to job", user.id, None, ZERO_AMT)
    container_mode = quote.shipment_type if quote.shipment_type in ("FCL", "LCL") else None
    job = Shipment(organization_id=org_id, job_number=_next_job_number(db, org_id, quote.mode, quote.direction, quote.shipment_type if quote.shipment_type in ("FCL", "LCL") else None, user), mode=quote.mode, direction=quote.direction, container_mode=container_mode,
                   pol=quote.pol, pod=quote.pod, customer_id=quote.customer_id, status="Open", created_by=user.id)
    db.add(job)
    db.flush()
    from . import job_lifecycle as jl

    jl.on_job_created(db, job, user, "quotation conversion", {"quotation": quote.number})
    _copy_quote_to_job(db, org_id, quote, job, sell, buy)
    quote.status = "CONVERTED"
    quote.job_number = job.job_number
    _event(db, quote, "CONVERTED", "ACCEPTED", "CONVERTED", user, job.job_number)
    record_audit(db, "QUOTATION_CONVERTED", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number, "job_number": job.job_number})
    db.commit()
    return _out(db, _load(db, org_id, quote_id), user, True)
