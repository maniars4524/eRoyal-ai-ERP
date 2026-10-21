from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session, selectinload

from .auth import permission_required
from .auth_api import record_audit
from .billing import CREDIT_TARGET, DOC_TYPES, calc_line, doc_side, fiscal_year, invoice_kind, line_group, next_number, outstanding, payment_status, q2
from .db import get_db
from . import gl
from . import job_lifecycle as jl
from .models import Customer, Document, DocumentLine, JobCharge, Shipment, User, OrgBranch

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
    tax_type: str = Field(default="Taxable", pattern="^(Taxable|Non-Taxable|Pure Agent|RCM)$")
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=2)
    job_number: str | None = Field(default=None, max_length=40)


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


def proforma_required(db: Session, org_id: int) -> bool:
    """Company setting INVOICE_REQUIRES_PROFORMA: a customer invoice (Tax Invoice) can be generated only from a confirmed Proforma Invoice."""
    return jl.setting_enabled(db, org_id, "INVOICE_REQUIRES_PROFORMA")


def _out(doc: Document, with_lines: bool = False, required: bool | None = None) -> dict:
    data = {
        "id": doc.id, "doc_type": doc.doc_type, "doc_number": doc.doc_number, "status": doc.status, "payment_status": payment_status(doc),
        "doc_date": doc.doc_date, "due_date": doc.due_date, "job_number": doc.job_number, "customer_id": doc.customer_id, "party_name": doc.party_name,
        "party_gstin": doc.party_gstin, "place_of_supply": doc.place_of_supply, "supply_type": doc.supply_type, "ref_document_id": doc.ref_document_id,
        "remarks": doc.remarks, "header": doc.header or {}, "subtotal": _money(doc.subtotal), "tax_total": _money(doc.tax_total), "grand_total": _money(doc.grand_total),
        "paid_amount": _money(doc.paid_amount), "credited_amount": _money(doc.credited_amount), "outstanding": _money(outstanding(doc)),
        "posted_at": doc.posted_at, "created_at": doc.created_at, "proforma_required": bool(required) if doc.doc_type == "invoice" else False,
        "invoice_kind": invoice_kind([l.tax_type for l in doc.lines]) if doc.lines else "TAX",
    }
    if doc.supply_type == "intra":
        half = q2(doc.tax_total / 2)
        data["tax_breakup"] = {"cgst": _money(half), "sgst": _money(doc.tax_total - half), "igst": "0.00"}
    else:
        data["tax_breakup"] = {"cgst": "0.00", "sgst": "0.00", "igst": _money(doc.tax_total)}
    if with_lines:
        data["round_off"] = str((doc.header or {}).get("round_off", "0.00"))
        data["lines"] = [{"line_no": l.line_no, "job_number": l.job_number, "charge_name": l.charge_name, "description": l.description, "hsn": l.hsn, "uom": l.uom, "qty": str(l.qty), "ccy": l.ccy, "exch": str(l.exch), "rate": str(l.rate), "tax_type": l.tax_type, "tax_pct": str(l.tax_pct), "amount": _money(l.amount), "tax_amount": _money(l.tax_amount), "line_total": _money(l.line_total)} for l in doc.lines]
    return data


def _apply_lines(doc: Document, lines: list[LineIn]) -> None:
    for line in lines:
        if line.ccy != "INR" and line.exch <= 0:
            raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR lines")
    doc.lines.clear()
    session = object_session(doc)
    if session is not None:
        session.flush()  # delete the old lines first so line numbers can be reused
    if doc.doc_type in ("invoice", "debit_note", "credit_note") and len({line_group(l.tax_type) for l in lines}) > 1:
        raise HTTPException(status_code=422, detail="Pure Agent (reimbursement) and RCM entries are billed on their own invoice. Keep Taxable / Non-Taxable entries together and move the Pure Agent or RCM entries to a separate invoice")
    subtotal = tax = Decimal(0)
    for index, line in enumerate(lines, start=1):
        c = calc_line(line.qty, line.ccy, Decimal(1) if line.ccy == "INR" else line.exch, line.rate, line.tax_type, line.tax_pct if line.tax_type == "Taxable" else Decimal(0))
        doc.lines.append(DocumentLine(line_no=index, job_number=(line.job_number or None), charge_name=line.charge_name.strip(), description=line.description.strip(), hsn=line.hsn.strip(), uom=line.uom, qty=line.qty, ccy=line.ccy, exch=Decimal(1) if line.ccy == "INR" else line.exch, rate=line.rate, tax_type=line.tax_type, tax_pct=line.tax_pct if line.tax_type in ("Taxable", "RCM") else Decimal(0), **c))
        subtotal += c["amount"]
        tax += c["tax_amount"]
    total = subtotal + tax
    header = dict(doc.header or {})
    rounding = doc.doc_type in ("invoice", "debit_note", "credit_note", "purchase_invoice", "purchase_credit_note") and not header.get("no_round_off")
    round_off = (Decimal(round(total)) - total) if rounding else Decimal(0)
    header["round_off"] = str(q2(round_off))
    doc.header = header
    doc.subtotal, doc.tax_total, doc.grand_total = subtotal, tax, total + round_off


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
    if job is not None:
        jl.assert_job_editable(job, "its invoices")
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
    assign_proforma_no(db, org_id, doc)
    jl.doc_hook(db, doc, user, "DRAFT_CREATED")
    return doc


@router.get("")
def list_documents(
    doc_type: str | None = Query(default=None, pattern=DOC_TYPE_PATTERN),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(DRAFT|CONFIRMED|POSTED|CANCELLED)$"),
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
        line_docs = select(DocumentLine.document_id).where(DocumentLine.job_number == job_number)
        query = query.where(or_(Document.job_number == job_number, Document.id.in_(line_docs)))
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


def assign_proforma_no(db: Session, org_id: int, doc: Document) -> None:
    """A customer invoice carries its proforma number from the moment the draft is saved (PF/<fiscal year>/<running no>); the number never changes afterwards."""
    from .models import HblCounter

    header = dict(doc.header or {})
    if doc.doc_type != "invoice" or header.get("proforma_no"):
        return
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == org_id, HblCounter.hbl_type == "PF").with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=org_id, hbl_type="PF", last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    header["proforma_no"] = f"PF/{fiscal_year(doc.doc_date)}/{ctr.last_no:05d}"
    header.setdefault("proforma_date", date.today().isoformat())
    doc.header = header


@router.post("", status_code=201)
def create_document(payload: DocumentIn, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _new_document(db, user, org_id, payload)
    record_audit(db, "DOCUMENT_CREATED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"party": doc.party_name})
    db.commit()
    return _out(_load(db, org_id, doc.id), True, proforma_required(db, org_id))


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
    def make_line(c):
        return LineIn(charge_name=c.charge_name, description=c.description, hsn=c.hsn, uom=c.uom, qty=c.qty, ccy=(c.sell_ccy if sell else c.buy_ccy), exch=(c.sell_exch if sell else c.buy_exch), rate=(c.sell_rate if sell else c.buy_rate),
                      tax_type=(c.sell_tax_type if sell else c.buy_tax_type), tax_pct=(c.sell_tax_pct if sell else c.buy_tax_pct))

    groups: dict[str, list] = {}
    for c in usable:
        groups.setdefault(line_group(c.sell_tax_type if sell else c.buy_tax_type), []).append(make_line(c))
    today = date.today()
    made, skipped = [], []
    existing = list(db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.shipment_id == job.id, Document.doc_type == payload.doc_type, Document.status != "CANCELLED", Document.party_name == party)))
    have = {line_group(l.tax_type) for d in existing for l in d.lines}
    for grp in ("TAX", "PURE_AGENT", "RCM"):
        if grp not in groups:
            continue
        if grp in have:
            skipped.append(grp)
            continue
        doc = _new_document(db, user, org_id, DocumentIn(doc_type=payload.doc_type, doc_date=today, due_date=today + timedelta(days=payload.due_days), job_number=job.job_number, party_name=party, lines=groups[grp]))
        record_audit(db, "DOCUMENT_CREATED_FROM_JOB", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"job_number": job.job_number, "party": party, "lines": len(groups[grp]), "kind": grp})
        made.append(doc)
    if not made:
        first = existing[0]
        raise HTTPException(status_code=409, detail=f"A {payload.doc_type.replace('_', ' ')} already exists for this job and party ({first.doc_number or 'draft #' + str(first.id)}). Cancel it first, or issue a debit/credit note.")
    db.commit()
    out = _out(_load(db, org_id, made[0].id), True, proforma_required(db, org_id))
    out["also_created"] = [{"id": d.id, "invoice_kind": invoice_kind([l.tax_type for l in _load(db, org_id, d.id).lines])} for d in made[1:]]
    return out


@router.get("/{doc_id}")
def get_document(doc_id: int, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    d = _load(db, _tenant(user), doc_id, lock=True)
    if d.doc_type == "invoice" and d.status == "DRAFT" and not (d.header or {}).get("proforma_no"):   # a draft saved before proforma numbers existed
        assign_proforma_no(db, _tenant(user), d)
        db.commit()
    return _out(_load(db, _tenant(user), doc_id), True, proforma_required(db, _tenant(user)))


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
    kept = {k: v for k, v in (doc.header or {}).items() if k in ("proforma_no", "proforma_date")}
    for key, value in changes.items():
        if key == "lines" or (value is None and key not in ("due_date", "customer_id", "ref_document_id", "remarks", "party_gstin", "place_of_supply")):
            continue
        setattr(doc, key, value)
    if kept and "header" in changes:
        doc.header = {**(doc.header or {}), **kept}
    if lines is not None:
        _apply_lines(doc, lines)
    elif "header" in changes and doc.lines:   # e.g. the round-off switch: the totals are worked out again from the same lines
        _apply_lines(doc, [LineIn(charge_name=l.charge_name, description=l.description, hsn=l.hsn, uom=l.uom, qty=l.qty, ccy=l.ccy, exch=l.exch, rate=l.rate, tax_type=l.tax_type, tax_pct=l.tax_pct, job_number=l.job_number) for l in doc.lines])
    assign_proforma_no(db, org_id, doc)   # drafts saved before proforma numbers existed get theirs on the next save
    record_audit(db, "DOCUMENT_UPDATED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"fields": sorted(k for k in changes if k != "lines"), "lines": None if lines is None else len(lines)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


@router.post("/{doc_id}/post")
def post_document(doc_id: int, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """DRAFT -> POSTED: assigns the legal document number and freezes the document."""
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.status not in ("DRAFT", "CONFIRMED"):
        raise HTTPException(status_code=409, detail="Only draft documents can be posted")
    if doc.doc_type == "invoice" and doc.status == "DRAFT" and proforma_required(db, org_id):
        raise HTTPException(status_code=409, detail="Confirm the Proforma Invoice first. The Tax Invoice is generated only from a confirmed proforma")
    jl.guard_document_job(db, doc.shipment_id)
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
    branch_ref = (doc.header or {}).get("party_branch_id")
    if branch_ref not in (None, ""):
        br = db.scalar(select(OrgBranch).where(OrgBranch.id == int(branch_ref), OrgBranch.organization_id == org_id, OrgBranch.customer_id == doc.customer_id))
        if not br or br.status != "ACTIVE":
            raise HTTPException(status_code=422, detail="The selected billing branch does not belong to this organization or is inactive")
        if not br.billing_enabled and doc.doc_type in ("invoice", "debit_note", "credit_note"):
            raise HTTPException(status_code=422, detail=f"Branch {br.code} is not billing-enabled; choose a billing-enabled branch for this invoice")
    if doc.doc_type in ("invoice", "debit_note") and doc.customer_id:
        from .credit_control import enforce as credit_enforce

        credit_enforce(db, org_id, doc.customer_id, "post invoice", user.id, int(branch_ref) if branch_ref not in (None, "") else None, doc.grand_total if not doc.shipment_id else Decimal(0))
    prefix = DOC_TYPES[doc.doc_type][0]
    doc.doc_number = next_number(db, org_id, doc.doc_type, prefix, doc.doc_date, user=user)
    doc.status = "POSTED"
    doc.posted_at = datetime.now(timezone.utc)
    doc.posted_by = user.id
    jl.doc_hook(db, doc, user, "POSTED")
    gl.sync_document(db, doc, user.id)
    record_audit(db, "DOCUMENT_POSTED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"number": doc.doc_number, "total": str(doc.grand_total)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


def _proforma_checks(doc: Document) -> None:
    if not doc.lines or doc.grand_total <= 0:
        raise HTTPException(status_code=422, detail="A proforma needs at least one line with a positive total before it is confirmed")
    if not doc.party_name:
        raise HTTPException(status_code=422, detail="Party name is required before the proforma is confirmed")


@router.post("/{doc_id}/confirm")
def confirm_proforma(doc_id: int, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """DRAFT proforma -> CONFIRMED proforma (the customer has agreed). Gives it a proforma number; the Tax Invoice is generated from it."""
    from .models import HblCounter

    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.doc_type != "invoice":
        raise HTTPException(status_code=422, detail="Only a customer invoice has a proforma stage")
    if doc.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only a draft proforma can be confirmed")
    jl.guard_document_job(db, doc.shipment_id)
    _proforma_checks(doc)
    assign_proforma_no(db, org_id, doc)
    header = dict(doc.header or {})
    header.update({"proforma_date": date.today().isoformat(), "proforma_confirmed_by": user.id})
    doc.header = header
    doc.status = "CONFIRMED"
    record_audit(db, "PROFORMA_CONFIRMED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"proforma_no": header["proforma_no"], "total": str(doc.grand_total)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


@router.post("/{doc_id}/reopen-proforma")
def reopen_proforma(doc_id: int, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.doc_type != "invoice" or doc.status != "CONFIRMED":
        raise HTTPException(status_code=409, detail="Only a confirmed proforma can be reopened for changes")
    doc.status = "DRAFT"
    record_audit(db, "PROFORMA_REOPENED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"proforma_no": (doc.header or {}).get("proforma_no")})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


@router.post("/{doc_id}/e-invoice")
def create_einvoice(doc_id: int, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """e-Invoice for a posted B2B Tax Invoice (on the customer's request): the IRN and the QR code printed on the original.
    The IRN is computed here as a SHA-256 hash of supplier GSTIN, financial year, document type and document number. A legally valid IRN is the one issued by the GST
    e-invoice portal (IRP); it is registered only when the company's GSP credentials are connected, so until then it is marked as not yet registered."""
    import hashlib
    import io
    import json

    import segno

    from .models import CompanyDocProfile, Organization

    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.doc_type not in ("invoice", "debit_note", "credit_note") or doc.status != "POSTED":
        raise HTTPException(status_code=409, detail="An e-Invoice is created from a posted Tax Invoice, Debit Note or Credit Note")
    if invoice_kind([l.tax_type for l in doc.lines]) != "TAX":
        raise HTTPException(status_code=422, detail="An e-Invoice applies to taxable B2B tax documents. Non-taxable, pure agent (reimbursement) and RCM invoices do not carry an IRN")
    header = dict(doc.header or {})
    if header.get("einvoice"):
        return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))
    prof = db.scalar(select(CompanyDocProfile).where(CompanyDocProfile.organization_id == org_id))
    pd = (prof.data if prof else {}) or {}
    seller = str(pd.get("gstin") or "").strip().upper()
    buyer = str(doc.party_gstin or "").strip().upper()
    if not seller:
        raise HTTPException(status_code=422, detail="Enter the company's GSTIN in the Company letterhead & registration profile first")
    if not buyer:
        raise HTTPException(status_code=422, detail="An e-Invoice is for a registered (GSTIN) customer. This invoice has no customer GSTIN")
    fy = "20" + fiscal_year(doc.doc_date)[:2] + "-" + fiscal_year(doc.doc_date)[3:]
    dtype = {"invoice": "INV", "debit_note": "DBN", "credit_note": "CRN"}[doc.doc_type]
    irn = hashlib.sha256(f"{seller}{fy}{dtype}{doc.doc_number}".encode()).hexdigest()
    irn_dt = datetime.now(timezone.utc)
    main_hsn = next((l.hsn for l in sorted(doc.lines, key=lambda x: -x.amount) if l.hsn), "")
    payload = {"SellerGstin": seller, "BuyerGstin": buyer, "DocNo": doc.doc_number, "DocTyp": dtype, "DocDt": doc.doc_date.strftime("%d/%m/%Y"), "TotInvVal": float(doc.grand_total), "ItemCnt": len(doc.lines), "MainHsnCode": main_hsn, "Irn": irn, "IrnDt": irn_dt.strftime("%Y-%m-%d %H:%M:%S")}
    buf = io.BytesIO()
    segno.make(json.dumps(payload, separators=(",", ":")), error="m").save(buf, kind="svg", scale=4, border=1, xmldecl=False, svgns=True, nl=False)
    qr = "data:image/svg+xml;base64," + __import__("base64").b64encode(buf.getvalue()).decode()
    header["einvoice"] = {"irn": irn, "irn_date": irn_dt.isoformat(), "qr": qr, "qr_payload": payload, "ack_no": None, "source": "LOCAL", "registered": False, "created_by": user.id}
    doc.header = header
    record_audit(db, "EINVOICE_CREATED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"number": doc.doc_number, "irn": irn, "registered": False})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


@router.get("/{doc_id}/payments")
def document_payments(doc_id: int, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """The receipts (or payments) settled against this document, one row per receipt with its transaction details, and the credit notes adjusted against it."""
    from .models import Payment, PaymentAllocation

    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id)
    rows = []
    for a in db.scalars(select(PaymentAllocation).where(PaymentAllocation.document_id == doc.id).order_by(PaymentAllocation.id)):
        p = db.get(Payment, a.payment_id)
        if not p or p.organization_id != org_id:
            continue
        others = []
        for b in p.allocations:
            d2 = db.get(Document, b.document_id)
            others.append({"document_id": b.document_id, "doc_number": d2.doc_number if d2 else None, "doc_type": d2.doc_type if d2 else None, "job_number": d2.job_number if d2 else None, "amount": str(b.amount)})
        rows.append({"payment_id": p.id, "pay_number": p.pay_number, "pay_date": p.pay_date, "direction": p.direction, "method": p.method, "reference": p.reference, "party_name": p.party_name, "status": p.status, "remarks": p.remarks,
                     "total_amount": str(p.amount), "applied_to_this": str(a.amount), "tds_to_this": str(a.tds_amount or 0), "round_off_to_this": str(a.round_off or 0), "details": p.details or {}, "against": others})
    credits = [{"id": c.id, "doc_number": c.doc_number, "doc_type": c.doc_type, "doc_date": c.doc_date, "amount": str(c.grand_total), "status": c.status}
               for c in db.scalars(select(Document).where(Document.organization_id == org_id, Document.ref_document_id == doc.id, Document.status != "CANCELLED").order_by(Document.id))]
    return {"document_id": doc.id, "doc_number": doc.doc_number, "total": str(doc.grand_total), "paid": str(doc.paid_amount), "credited": str(doc.credited_amount), "outstanding": str(outstanding(doc)), "payments": rows, "credit_notes": credits}


class AddJob(BaseModel):
    job_number: str = Field(max_length=40)


@router.get("/billing/billable-jobs")
def billable_jobs(party: str = Query(max_length=200), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Other jobs whose sell charges can be added to an invoice of this party: the job's customer, or the client named on its charges, is the party."""
    org_id = _tenant(user)
    cust = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == party))
    out = []
    for job in db.scalars(select(Shipment).where(Shipment.organization_id == org_id).order_by(Shipment.id.desc()).limit(500)):
        charges = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == org_id, JobCharge.sell_rate > 0)))
        if not charges or not ((cust and job.customer_id == cust.id) or any(c.client == party for c in charges)):
            continue
        billed = {(l.job_number or d.job_number, l.charge_name) for d in db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.doc_type == "invoice", Document.status != "CANCELLED", Document.party_name == party)) for l in d.lines}
        open_lines = [c for c in charges if (job.job_number, c.charge_name) not in billed]
        if open_lines:
            out.append({"job_number": job.job_number, "mode": job.mode, "direction": job.direction, "pol": job.pol, "pod": job.pod, "lines": len(open_lines), "amount": str(sum((c.qty * c.sell_rate * (1 if c.sell_ccy == "INR" else c.sell_exch) for c in open_lines), Decimal(0)))})
    return out


@router.post("/{doc_id}/add-job")
def add_job_to_invoice(doc_id: int, body: AddJob, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    """Get the sell charges of another job of the same party into this draft invoice, so the party receives a single invoice for several jobs. Every line keeps its job number."""
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.doc_type != "invoice" or doc.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Jobs can be added to a draft invoice only")
    job = _job(db, org_id, body.job_number)
    charges = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == org_id, JobCharge.sell_rate > 0).order_by(JobCharge.line_no)))
    cust = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == doc.party_name))
    if not ((cust and job.customer_id == cust.id) or any(c.client == doc.party_name for c in charges)):
        raise HTTPException(status_code=422, detail=f"Job {job.job_number} is not billed to {doc.party_name}. Only jobs of the same billing party can be added")
    if not charges:
        raise HTTPException(status_code=422, detail=f"Job {job.job_number} has no sell charges to bill")
    have = {(l.job_number or doc.job_number, l.charge_name) for l in doc.lines}
    new = [c for c in charges if (job.job_number, c.charge_name) not in have]
    if not new:
        raise HTTPException(status_code=409, detail=f"The sell charges of {job.job_number} are already on this invoice")
    existing = [LineIn(charge_name=l.charge_name, description=l.description, hsn=l.hsn, uom=l.uom, qty=l.qty, ccy=l.ccy, exch=l.exch, rate=l.rate, tax_type=l.tax_type, tax_pct=l.tax_pct, job_number=l.job_number or doc.job_number) for l in doc.lines]
    added = [LineIn(charge_name=c.charge_name, description=c.description, hsn=c.hsn, uom=c.uom, qty=c.qty, ccy=c.sell_ccy, exch=c.sell_exch, rate=c.sell_rate, tax_type=c.sell_tax_type, tax_pct=c.sell_tax_pct, job_number=job.job_number) for c in new]
    if len({line_group(l.tax_type) for l in existing + added}) > 1:
        raise HTTPException(status_code=422, detail="That job has Pure Agent or RCM entries that must be billed on their own invoice. Raise those separately")
    _apply_lines(doc, existing + added)
    record_audit(db, "JOB_ADDED_TO_INVOICE", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"job_number": job.job_number, "lines": len(added)})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))


@router.post("/{doc_id}/cancel")
def cancel_document(doc_id: int, user: User = Depends(permission_required("billing.cancel")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    doc = _load(db, org_id, doc_id, lock=True)
    if doc.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Document is already cancelled")
    jl.guard_document_job(db, doc.shipment_id)
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
    jl.doc_hook(db, doc, user, "CANCELLED")
    gl.sync_document(db, doc, user.id)
    record_audit(db, "DOCUMENT_CANCELLED", "BILLING", user, org_id, doc.doc_type, str(doc.id), {"number": doc.doc_number})
    db.commit()
    return _out(_load(db, org_id, doc_id), True, proforma_required(db, org_id))
