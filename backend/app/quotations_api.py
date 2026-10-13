from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session, selectinload

from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number, q2
from .db import get_db
from .models import Customer, JobCharge, Quotation, QuotationLine, Shipment, User

router = APIRouter(prefix="/api/quotations", tags=["quotations"])
MODES = "^(Sea|Air|Road|Rail|Multimodal|Courier)$"
DIRECTIONS = "^(Export|Import|Domestic|Cross-Trade)$"


class QLine(BaseModel):
    charge_name: str = Field(default="", max_length=160)
    carrier: str = Field(default="", max_length=160)
    basis: str = Field(default="Per Shipment", max_length=40)
    ccy: str = Field(default="INR", pattern="^[A-Z]{3}$")
    exch: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=6)
    rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    qty: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=3)
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=2)


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
    lines: list[QLine] = Field(default_factory=list, max_length=200)


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
    lines: list[QLine] | None = Field(default=None, max_length=200)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quotations are tenant-scoped; Super Admin does not manage them")
    return user.organization_id


def _line_amount(l: QuotationLine) -> Decimal:
    gross = l.qty * l.rate
    return q2(gross if l.ccy == "INR" else gross * l.exch)


def effective_status(q: Quotation) -> str:
    if q.status == "SENT" and q.valid_until and q.valid_until < date.today():
        return "EXPIRED"
    return q.status


def _out(q: Quotation, with_lines: bool = False) -> dict:
    by_carrier: dict[str, Decimal] = defaultdict(Decimal)
    for l in q.lines:
        by_carrier[l.carrier or "—"] += l.amount
    totals = {k: str(v) for k, v in by_carrier.items()}
    quoted = min(by_carrier.values()) if by_carrier else Decimal(0)
    data = {"id": q.id, "number": q.number, "status": q.status, "effective_status": effective_status(q), "quote_date": q.quote_date, "valid_until": q.valid_until, "customer_id": q.customer_id, "party_name": q.party_name,
            "mode": q.mode, "direction": q.direction, "shipment_type": q.shipment_type, "quote_type": q.quote_type, "carrier_mode": q.carrier_mode, "pol": q.pol, "pod": q.pod, "terms": q.terms, "remarks": q.remarks, "header": q.header or {},
            "job_number": q.job_number, "quoted_amount": str(quoted), "carrier_totals": totals, "sent_at": q.sent_at, "responded_at": q.responded_at, "created_at": q.created_at}
    if with_lines:
        data["lines"] = [{"line_no": l.line_no, "charge_name": l.charge_name, "carrier": l.carrier, "basis": l.basis, "ccy": l.ccy, "exch": str(l.exch), "rate": str(l.rate), "qty": str(l.qty), "tax_pct": str(l.tax_pct), "amount": str(l.amount)} for l in q.lines]
    return data


def _apply_lines(q: Quotation, lines: list[QLine]) -> None:
    for line in lines:
        if line.ccy != "INR" and line.exch <= 0:
            raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR lines")
    q.lines.clear()
    session = object_session(q)
    if session is not None:
        session.flush()
    for i, line in enumerate(lines, start=1):
        row = QuotationLine(line_no=i, charge_name=line.charge_name.strip(), carrier=line.carrier.strip(), basis=line.basis, ccy=line.ccy, exch=Decimal(1) if line.ccy == "INR" else line.exch, rate=line.rate, qty=line.qty, tax_pct=line.tax_pct)
        row.amount = _line_amount(row)
        q.lines.append(row)


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
def list_quotations(status_filter: str | None = Query(default=None, alias="status", pattern="^(DRAFT|SENT|ACCEPTED|REJECTED|CONVERTED|EXPIRED)$"), q: str | None = Query(default=None, max_length=100),
                    limit: int = Query(default=300, ge=1, le=1000), user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    query = select(Quotation).options(selectinload(Quotation.lines)).where(Quotation.organization_id == org_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Quotation.number.ilike(like), Quotation.party_name.ilike(like), Quotation.pol.ilike(like), Quotation.pod.ilike(like)))
    rows = [_out(x) for x in db.scalars(query.order_by(Quotation.quote_date.desc(), Quotation.id.desc()).limit(limit))]
    if status_filter:
        rows = [r for r in rows if r["effective_status"] == status_filter]
    return rows


@router.post("", status_code=201)
def create_quotation(payload: QuotationIn, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    _check_customer(db, org_id, payload.customer_id)
    today = date.today()
    quote = Quotation(organization_id=org_id, status="DRAFT", quote_date=payload.quote_date or today, valid_until=payload.valid_until or today + timedelta(days=15), created_by=user.id,
                      **payload.model_dump(exclude={"lines", "quote_date", "valid_until"}))
    _apply_lines(quote, payload.lines)
    db.add(quote)
    db.flush()
    record_audit(db, "QUOTATION_CREATED", "SALES", user, org_id, "Quotation", str(quote.id), {"party": quote.party_name})
    db.commit()
    return _out(_load(db, org_id, quote.id), True)


@router.get("/{quote_id}")
def get_quotation(quote_id: int, user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    return _out(_load(db, _tenant(user), quote_id), True)


@router.patch("/{quote_id}")
def update_quotation(quote_id: int, payload: QuotationPatch, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft quotations can be edited; a sent quotation is locked")
    changes = payload.model_dump(exclude_unset=True)
    if "customer_id" in changes:
        _check_customer(db, org_id, changes["customer_id"])
    for key, value in changes.items():
        if key == "lines" or (value is None and key not in ("customer_id", "valid_until", "terms", "remarks", "shipment_type", "quote_type", "pol", "pod")):
            continue
        setattr(quote, key, value)
    if payload.lines is not None:
        _apply_lines(quote, payload.lines)
    record_audit(db, "QUOTATION_UPDATED", "SALES", user, org_id, "Quotation", str(quote.id), {"fields": sorted(k for k in changes if k != "lines")})
    db.commit()
    return _out(_load(db, org_id, quote_id), True)


@router.delete("/{quote_id}", status_code=204)
def delete_draft(quote_id: int, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft quotations can be deleted")
    record_audit(db, "QUOTATION_DRAFT_DELETED", "SALES", user, org_id, "Quotation", str(quote.id), {"party": quote.party_name})
    db.delete(quote)
    db.commit()


@router.post("/{quote_id}/send")
def send_quotation(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """DRAFT -> SENT: assigns the quotation number and locks the quotation."""
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft quotations can be sent")
    if not quote.party_name:
        raise HTTPException(status_code=422, detail="Client is required before sending")
    if not quote.lines or not any(l.rate > 0 for l in quote.lines):
        raise HTTPException(status_code=422, detail="Add at least one charge with a rate before sending")
    if quote.valid_until and quote.valid_until < date.today():
        raise HTTPException(status_code=422, detail="The validity date has already passed")
    quote.number = next_number(db, org_id, "quotation", "QT", quote.quote_date)
    quote.status = "SENT"
    quote.sent_at = datetime.now(timezone.utc)
    record_audit(db, "QUOTATION_SENT", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number})
    db.commit()
    return _out(_load(db, org_id, quote_id), True)


def _respond(quote_id: int, outcome: str, user: User, db: Session) -> dict:
    org_id = _tenant(user)
    quote = _load(db, org_id, quote_id, lock=True)
    if effective_status(quote) != "SENT":
        raise HTTPException(status_code=409, detail="Only a sent, unexpired quotation can be accepted or rejected")
    quote.status = outcome
    quote.responded_at = datetime.now(timezone.utc)
    record_audit(db, f"QUOTATION_{outcome}", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number})
    db.commit()
    return _out(_load(db, org_id, quote_id), True)


@router.post("/{quote_id}/accept")
def accept(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    return _respond(quote_id, "ACCEPTED", user, db)


@router.post("/{quote_id}/reject")
def reject(quote_id: int, user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    return _respond(quote_id, "REJECTED", user, db)


@router.post("/{quote_id}/convert")
def convert_to_job(quote_id: int, carrier: str | None = Query(default=None, max_length=160), user: User = Depends(permission_required("quote.send")), db: Session = Depends(get_db)):
    """ACCEPTED -> CONVERTED: creates a job and copies the (chosen carrier's) quoted charges onto its sell side."""
    org_id = _tenant(user)
    if not has_permission(user, "shipment.create"):
        raise HTTPException(status_code=403, detail="Permission denied: creating the job needs shipment.create")
    quote = _load(db, org_id, quote_id, lock=True)
    if quote.status != "ACCEPTED":
        raise HTTPException(status_code=409, detail="Only an accepted quotation can be converted to a job")
    lines = [l for l in quote.lines if l.rate > 0]
    carriers = sorted({l.carrier for l in lines if l.carrier})
    if len(carriers) > 1:
        if not carrier or carrier not in carriers:
            raise HTTPException(status_code=422, detail="This quotation compares several carriers (" + ", ".join(carriers) + "); choose which carrier's rates to convert")
        lines = [l for l in lines if l.carrier == carrier]
    from .shipments_api import _next_job_number  # local import: shipments_api imports auth helpers too

    container_mode = quote.shipment_type if quote.shipment_type in ("FCL", "LCL") else None
    job = Shipment(organization_id=org_id, job_number=_next_job_number(db, org_id, quote.mode, quote.direction), mode=quote.mode, direction=quote.direction, container_mode=container_mode,
                   pol=quote.pol, pod=quote.pod, customer_id=quote.customer_id, status="Open", created_by=user.id)
    db.add(job)
    db.flush()
    for i, l in enumerate(lines, start=1):
        db.add(JobCharge(organization_id=org_id, shipment_id=job.id, line_no=i, charge_name=l.charge_name, description=f"From quotation {quote.number}", uom=l.basis, qty=l.qty, hsn="",
                         sell_ccy=l.ccy, sell_exch=l.exch, sell_rate=l.rate, sell_tax_type="Taxable" if l.tax_pct > 0 else "Non-Taxable", sell_tax_pct=l.tax_pct, client=quote.party_name, use_client=True))
    job.revenue = sum((l.amount for l in lines), Decimal(0))
    quote.status = "CONVERTED"
    quote.job_number = job.job_number
    record_audit(db, "QUOTATION_CONVERTED", "SALES", user, org_id, "Quotation", str(quote.id), {"number": quote.number, "job_number": job.job_number})
    db.commit()
    return _out(_load(db, org_id, quote_id), True)
