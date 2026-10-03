from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .auth_api import record_audit
from .billing import CREDIT_TARGET, DOC_TYPES, calc_line, doc_side, next_number, outstanding, payment_status, q2
from .db import get_db
from .models import Customer, Document, DocumentLine, JobCharge, Shipment, User

router = APIRouter(prefix="/api/documents", tags=["billing-documents"])
DOC_TYPE_PATTERN = "^(invoice|debit_note|credit_note|purchase_invoice|purchase_credit_note)$"


class LineIn(BaseModel):
    charge_name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=240)
    hsn: str = Field(default="", max_length=20)
    uom: str = Field(default="Per Shipment", max_length=40)
    qty: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=3)
    ccy: str = Field(default="INR", pattern="^[A-Z]{3}$")
    exch: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=6)
    rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    tax_type: str = Field(default="Taxable", pattern="^(Taxable|Non-Taxable)$")
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=2)


class DocumentIn(BaseModel):
    doc_type: str = Field(pattern=DOC_TYPE_PATTERN)
    doc_date: date | None = None
    due_date: date | None = None
    job_number: str | None = Field(default=None, max_length=40)
    customer_id: int | None = None
    party_name: str = Field(default="", max_length=200)
    party_gstin: str | None = Field(default=None, max_length=15)
    place_of_supply: str | None = Field(default=None, max_length=60)
    supply_type: str = Field(default="inter", pattern="^(intra|inter)$")
    ref_document_id: int | None = None
    remarks: str | None = Field(default=None, max_length=2000)
    header: dict[str, Any] = Field(default_factory=dict)
    lines: list[LineIn] = Field(default_factory=list, max_length=200)


class DocumentPatch(BaseModel):
    doc_date: date | None = None
    due_date: date | None = None
    customer_id: int | None = None
    party_name: str | None = Field(default=None, max_length=200)
    party_gstin: str | None = Field(default=None, max_length=15)
    place_of_supply: str | None = Field(default=None, max_length=60)
    supply_type: str | None = Field(default=None, pattern="^(intra|inter)$")
    ref_document_id: int | None = None
    remarks: str | None = Field(default=None, max_length=2000)
    header: dict[str, Any] | None = None
    lines: list[LineIn] | None = Field(default=None, max_length=200)


class FromJob(BaseModel):
    job_number: str = Field(max_length=40)
    doc_type: str = Field(pattern="^(invoice|purchase_invoice)$")
    party: str | None = Field(default=None, max_length=160)
    due_days: int = Field(default=30, ge=0, le=365)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Billing documents are tenant-scoped; Super Admin does not manage them")
    return user.organization_id


def _money(v) -> str:
    return str(v)


def _out(doc: Document, with_lines: bool = False) -> dict:
    data = {
        "id": doc.id, "doc_type": doc.doc_type, "doc_number": doc.doc_number, "status": doc.status, "payment_status": payment_status(doc),
        "doc_date": doc.doc_date, "due_date": doc.due_date, "job_number": doc.job_number, "customer_id": doc.customer_id, "party_name": doc.party_name,
        "party_gstin": doc.party_gstin, "place_of_supply": doc.place_of_supply, "supply_type": doc.supply_type, "ref_document_id": doc.ref_document_id,
        "remarks": doc.remarks, "header": doc.header or {}, "subtotal": _money(doc.subtotal), "tax_total": _money(doc.tax_total), "grand_total": _money(doc.grand_total),
        "paid_amount": _money(doc.paid_amount), "credited_amount": _money(doc.credited_amount), "outstanding": _money(outstanding(doc)),
        "posted_at": doc.posted_at, "created_at": doc.created_at,
    }
    if doc.supply_type == "intra":
        half = q2(doc.tax_total / 2)
        data["tax_breakup"] = {"cgst": _money(half), "sgst": _money(doc.tax_total - half), "igst": "0.00"}
    else:
        data["tax_breakup"] = {"cgst": "0.00", "sgst": "0.00", "igst": _money(doc.tax_total)}
    if with_lines:
        data["lines"] = [{"line_no": l.line_no, "charge_name": l.charge_name, "description": l.description, "hsn": l.hsn, "uom": l.uom, "qty": str(l.qty), "ccy": l.ccy, "exch": str(l.exch), "rate": str(l.rate), "tax_type": l.tax_type, "tax_pct": str(l.tax_pct), "amount": _money(l.amount), "tax_amount": _money(l.tax_amount), "line_total": _money(l.line_total)} for l in doc.lines]
    return data


def _apply_lines(doc: Document, lines: list[LineIn]) -> None:
    for line in lines:
        if line.ccy != "INR" and line.exch <= 0:
            raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR lines")
    doc.lines.clear()
    subtotal = tax = Decimal(0)
    for index, line in enumerate(lines, start=1):
        c = calc_line(line.qty, line.ccy, Decimal(1) if line.ccy == "INR" else line.exch, line.rate, line.tax_type, line.tax_pct if line.tax_type == "Taxable" else Decimal(0))
        doc.lines.append(DocumentLine(line_no=index, charge_name=line.charge_name.strip(), description=line.description.strip(), hsn=line.hsn.strip(), uom=line.uom, qty=line.qty, ccy=line.ccy, exch=Decimal(1) if line.ccy == "INR" else line.exch, rate=line.rate, tax_type=line.tax_type, tax_pct=line.tax_pct if line.tax_type == "Taxable" else Decimal(0), **c))
        subtotal += c["amount"]
        tax += c["tax_amount"]
    doc.subtotal, doc.tax_total, doc.grand_total = subtotal, tax, subtotal + tax


def _load(db: Session, org_id: int, doc_id: int, lock: bool = False) -> Document:
    query = select(Document).options(selectinload(Document.lines)).where(Document.id == doc_id, Document.organization_id == org_id)
    if lock:
        query = query.with_for_update()
    doc = db.scalar(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _check_reference(db: Session, org_id: int, doc_type: str, ref_id: int | None) -> Document | None:
    if ref_id is None:
        return None
    ref = db.scalar(select(Document).where(Document.id == ref_id, Document.organization_id == org_id))
    if not ref:
        raise HTTPException(status_code=404, detail="Referenced document not found")
    if doc_type in CREDIT_TARGET and ref.doc_type != CREDIT_TARGET[doc_type]:
        raise HTTPException(status_code=422, detail=f"A {doc_type.replace('_', ' ')} must reference a {CREDIT_TARGET[doc_type].replace('_', ' ')}")
    return ref


def _job(db: Session, org_id: int, job_number: str | None) -> Shipment | None:
    if not job_number:
        return None
    job = db.scalar(select(Shipment).where(Shipment.organization_id == org_id, Shipment.job_number == job_number))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _new_document(db: Session, user: User, org_id: int, payload: DocumentIn) -> Document:
    job = _job(db, org_id, payload.job_number)
    if payload.customer_id and not db.scalar(select(Customer.id).where(Customer.id == payload.customer_id, Customer.organization_id == org_id)):
        raise HTTPException(status_code=404, detail="Customer not found")
    _check_reference(db, org_id, payload.doc_type, payload.ref_document_id)
    doc_date = payload.doc_date or date.today()
    doc = Document(organization_id=org_id, doc_type=payload.doc_type, status="DRAFT", doc_date=doc_date, due_date=payload.due_date, shipment_id=job.id if job else None, job_number=job.job_number if job else None,
                   customer_id=payload.customer_id or (job.customer_id if job else None), party_name=payload.party_name.strip(), party_gstin=payload.party_gstin, place_of_supply=payload.place_of_supply, supply_type=payload.supply_type,
                   ref_document_id=payload.ref_document_id, remarks=payload.remarks, header=payload.header, created_by=user.id)
    _apply_lines(doc, payload.lines)
    db.add(doc)
    db.flush()
    return doc


@router.get("")
def list_documents(
    doc_type: str | None = Query(default=None, pattern=DOC_TYPE_PATTERN),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(DRAFT|POSTED|CANCELLED)$"),
    job_number: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(permission_required("billing.read")),
    db: Session = Depends(get_db),
):
    org_id = _tenant(user)
    query = select(Document).where(Document.organization_id == org_id)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status_filter:
        query = query.where(Document.status == status_filter)
    if job_number:
        query = query.where(Document.job_number == job_number)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Document.doc_number.ilike(like), Document.party_name.ilike(like), Document.job_number.ilike(like)))
    return [_out(d) for d in db.scalars(query.order_by(Document.doc_date.desc(), Document.id.desc()).limit(limit).offset(offset))]


@router.get("/summary")
def summary(user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Receivable / payable position computed from posted documents (feeds the dashboard)."""
    org_id = _tenant(user)
    docs = db.scalars(select(Document).where(Document.organization_id == org_id, Document.status == "POSTED")).all()
    ar = sum((outstanding(d) for d in docs if d.doc_type in ("invoice", "debit_note")), Decimal(0))
    ap = sum((outstanding(d) for d in docs if d.doc_type == "purchase_invoice"), Decimal(0))
    today = date.today()
    overdue = sum((outstanding(d) for d in docs if d.doc_type in ("invoice", "debit_note") and d.due_date and d.due_date < today and outstanding(d) > 0), Decimal(0))
    month_start = today.replace(day=1)
    billed = sum((d.subtotal for d in docs if d.doc_type == "invoice" and d.doc_date >= month_start), Decimal(0))
    return {"receivable_outstanding": str(ar), "payable_outstanding": str(ap), "receivable_overdue": str(overdue), "invoiced_this_month": str(billed),
            "open_invoices": sum(1 for d in docs if d.doc_type == "invoice" and outstanding(d) > 0)}


@router.post("", status_code=201)
def create_document(payload: DocumentIn, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _new_document(db, user, org_id, payload)
    record_audit(db, "DOCUMENT_CREATED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"party": doc.party_name})
    db.commit()
    return _out(_load(db, org_id, doc.id), True)


@router.post("/from-job", status_code=201)
def from_job(payload: FromJob, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    """Create a DRAFT invoice (sell side) or purchase invoice (buy side, one vendor) from the job's saved charges."""
    org_id = _tenant(user)
    job = _job(db, org_id, payload.job_number)
    charges = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == org_id).order_by(JobCharge.line_no)))
    sell = payload.doc_type == "invoice"
    usable = [c for c in charges if (c.sell_rate if sell else c.buy_rate) > 0]
    if not usable:
        raise HTTPException(status_code=422, detail="This job has no " + ("sell" if sell else "buy") + " charges to bill")
    party = (payload.party or "").strip()
    if not sell:
        vendors = sorted({c.vendor for c in usable if c.vendor})
        if not party:
            if len(vendors) > 1:
                raise HTTPException(status_code=422, detail="This job has charges from several vendors (" + ", ".join(vendors) + "); choose the vendor to bill")
            party = vendors[0] if vendors else ""
        usable = [c for c in usable if c.vendor == party]
        if not usable:
            raise HTTPException(status_code=422, detail="No buy charges found for that vendor")
    else:
        if not party:
            customer = db.get(Customer, job.customer_id) if job.customer_id else None
            clients = sorted({c.client for c in usable if c.client})
            party = customer.name if customer else (clients[0] if clients else "")
    existing = db.scalar(select(Document).where(Document.organization_id == org_id, Document.shipment_id == job.id, Document.doc_type == payload.doc_type, Document.status != "CANCELLED", Document.party_name == party))
    if existing:
        raise HTTPException(status_code=409, detail=f"A {payload.doc_type.replace('_', ' ')} already exists for this job and party ({existing.doc_number or 'draft #' + str(existing.id)}). Cancel it first, or issue a debit/credit note.")
    lines = [LineIn(charge_name=c.charge_name, description=c.description, hsn=c.hsn, uom=c.uom, qty=c.qty, ccy=(c.sell_ccy if sell else c.buy_ccy), exch=(c.sell_exch if sell else c.buy_exch), rate=(c.sell_rate if sell else c.buy_rate), tax_type=(c.sell_tax_type if sell else c.buy_tax_type), tax_pct=(c.sell_tax_pct if sell else c.buy_tax_pct)) for c in usable]
    today = date.today()
    doc = _new_document(db, user, org_id, DocumentIn(doc_type=payload.doc_type, doc_date=today, due_date=today + timedelta(days=payload.due_days), job_number=job.job_number, party_name=party, lines=lines))
    record_audit(db, "DOCUMENT_CREATED_FROM_JOB", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"job_number": job.job_number, "party": party, "lines": len(lines)})
    db.commit()
    return _out(_load(db, org_id, doc.id), True)


@router.get("/{doc_id}")
def get_document(doc_id: int, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    return _out(_load(db, _tenant(user), doc_id), True)


@router.patch("/{doc_id}")
def update_document(doc_id: int, payload: DocumentPatch, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft documents can be edited; posted documents are corrected with a credit/debit note")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("customer_id") and not db.scalar(select(Customer.id).where(Customer.id == changes["customer_id"], Customer.organization_id == org_id)):
        raise HTTPException(status_code=404, detail="Customer not found")
    if "ref_document_id" in changes:
        _check_reference(db, org_id, doc.doc_type, changes["ref_document_id"])
    lines = payload.lines
    for key, value in changes.items():
        if key == "lines" or (value is None and key not in ("due_date", "customer_id", "ref_document_id", "remarks", "party_gstin", "place_of_supply")):
            continue
        setattr(doc, key, value)
    if lines is not None:
        _apply_lines(doc, lines)
    record_audit(db, "DOCUMENT_UPDATED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"fields": sorted(k for k in changes if k != "lines"), "lines": None if lines is None else len(lines)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True)


@router.post("/{doc_id}/post")
def post_document(doc_id: int, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """DRAFT -> POSTED: assigns the legal document number and freezes the document."""
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft documents can be posted")
    if not doc.lines or doc.grand_total <= 0:
        raise HTTPException(status_code=422, detail="A document needs at least one line with a positive total before posting")
    if not doc.party_name:
        raise HTTPException(status_code=422, detail="Party name is required before posting")
    if doc.doc_type in CREDIT_TARGET:
        if not doc.ref_document_id:
            raise HTTPException(status_code=422, detail="A credit note must reference the document it credits")
        ref = _load(db, org_id, doc.ref_document_id, lock=True)
        if ref.status != "POSTED":
            raise HTTPException(status_code=409, detail="The referenced document must be posted")
        if doc.grand_total > outstanding(ref):
            raise HTTPException(status_code=422, detail=f"Credit exceeds the outstanding balance of {ref.doc_number} ({outstanding(ref)})")
        ref.credited_amount += doc.grand_total
    prefix = DOC_TYPES[doc.doc_type][0]
    doc.doc_number = next_number(db, org_id, doc.doc_type, prefix, doc.doc_date)
    doc.status = "POSTED"
    doc.posted_at = datetime.now(timezone.utc)
    doc.posted_by = user.id
    record_audit(db, "DOCUMENT_POSTED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"number": doc.doc_number, "total": str(doc.grand_total)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True)


@router.post("/{doc_id}/cancel")
def cancel_document(doc_id: int, user: User = Depends(permission_required("billing.cancel")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Document is already cancelled")
    if doc.status == "POSTED":
        if doc.paid_amount > 0 or doc.credited_amount > 0:
            raise HTTPException(status_code=409, detail="This document has payments or credits against it; cancel those first or issue a credit note")
        dependants = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == org_id, Document.ref_document_id == doc.id, Document.status == "POSTED"))
        if dependants:
            raise HTTPException(status_code=409, detail="Credit notes have been posted against this document")
        if doc.doc_type in CREDIT_TARGET and doc.ref_document_id:
            ref = _load(db, org_id, doc.ref_document_id, lock=True)
            ref.credited_amount = max(Decimal(0), ref.credited_amount - doc.grand_total)
    doc.status = "CANCELLED"
    doc.cancelled_at = datetime.now(timezone.utc)
    record_audit(db, "DOCUMENT_CANCELLED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"number": doc.doc_number})
    db.commit()
    return _out(_load(db, org_id, doc_id), True)
