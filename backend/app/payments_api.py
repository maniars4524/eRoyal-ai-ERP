from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .auth_api import record_audit
from . import gl
from .billing import PAYABLE_TYPES, PAYMENT_PREFIX, next_number, outstanding, q2
from .db import get_db
from .models import Document, Payment, PaymentAllocation, User

router = APIRouter(prefix="/api/payments", tags=["payments"])


class Allocation(BaseModel):
    document_id: int
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)   # cash received / paid against the document
    tds: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)   # tax deducted at source by the payer, settles the document too
    round_off: Decimal = Field(default=Decimal(0), ge=0, le=100, max_digits=16, decimal_places=2)   # a small remainder written off to close the document


class PaymentIn(BaseModel):
    direction: str = Field(pattern="^(RECEIPT|PAYMENT)$")
    pay_date: date | None = None
    party_name: str = Field(default="", max_length=200)
    method: str = Field(default="Bank Transfer", pattern="^(Cash|Cheque|NEFT|RTGS|IMPS|UPI|Bank Transfer|Card|Demand Draft)$")
    reference: str | None = Field(default=None, max_length=80)
    details: dict | None = None
    kind: str | None = Field(default=None, pattern="^(full|part)$")
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    remarks: str | None = Field(default=None, max_length=2000)
    allocations: list[Allocation] = Field(default_factory=list, max_length=100)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payments are tenant-scoped; Super Admin does not manage them")
    return user.organization_id


def _out(p: Payment) -> dict:
    allocated = sum((a.amount for a in p.allocations), Decimal(0))
    return {"id": p.id, "direction": p.direction, "pay_number": p.pay_number, "pay_date": p.pay_date, "party_name": p.party_name, "method": p.method, "reference": p.reference, "amount": str(p.amount), "status": p.status,
            "allocated": str(allocated), "unallocated": str(p.amount - allocated), "remarks": p.remarks, "created_at": p.created_at, "details": p.details or {},
            "tds": str(sum((a.tds_amount or Decimal(0) for a in p.allocations), Decimal(0))), "settled": str(sum((a.amount + (a.tds_amount or Decimal(0)) + (a.round_off or Decimal(0)) for a in p.allocations), Decimal(0))), "round_off": str(sum((a.round_off or Decimal(0) for a in p.allocations), Decimal(0))),
            "allocations": [{"document_id": a.document_id, "amount": str(a.amount), "tds": str(a.tds_amount or 0), "round_off": str(a.round_off or 0)} for a in p.allocations]}


def _get(db: Session, org_id: int, payment_id: int) -> Payment:
    p = db.scalar(select(Payment).options(selectinload(Payment.allocations)).where(Payment.id == payment_id, Payment.organization_id == org_id))
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    return p


@router.get("")
def list_payments(direction: str | None = Query(default=None, pattern="^(RECEIPT|PAYMENT)$"), limit: int = Query(default=200, ge=1, le=1000), user: User = Depends(permission_required("payment.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    query = select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id)
    if direction:
        query = query.where(Payment.direction == direction)
    return [_out(p) for p in db.scalars(query.order_by(Payment.pay_date.desc(), Payment.id.desc()).limit(limit))]


@router.post("", status_code=201)
def create_payment(payload: PaymentIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80), user: User = Depends(permission_required("payment.create")), db: Session = Depends(get_db)):
    """Record a receipt/payment and allocate it to posted documents in one transaction.
    A repeated Idempotency-Key returns the original payment instead of creating a duplicate."""
    org_id = _tenant(user)
    if idempotency_key:
        existing = db.scalar(select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id, Payment.idempotency_key == idempotency_key))
        if existing:
            return _out(existing)
    if len({a.document_id for a in payload.allocations}) != len(payload.allocations):
        raise HTTPException(status_code=422, detail="Each document may appear only once in the allocations")
    total_alloc = sum((a.amount for a in payload.allocations), Decimal(0))
    if total_alloc > payload.amount:
        raise HTTPException(status_code=422, detail="Allocated amount exceeds the payment amount")
    docs = {}
    if payload.allocations:
        ids = sorted(a.document_id for a in payload.allocations)
        for doc in db.scalars(select(Document).where(Document.organization_id == org_id, Document.id.in_(ids)).order_by(Document.id).with_for_update()):
            docs[doc.id] = doc
    for alloc in payload.allocations:
        doc = docs.get(alloc.document_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document {alloc.document_id} not found")
        if doc.status != "POSTED" or doc.doc_type not in PAYABLE_TYPES[payload.direction]:
            raise HTTPException(status_code=422, detail=f"{doc.doc_number or doc.id} cannot be settled by a {payload.direction.lower()}")
        if alloc.amount + alloc.tds + alloc.round_off > outstanding(doc):
            raise HTTPException(status_code=422, detail=f"Allocation to {doc.doc_number} (with TDS) exceeds its outstanding balance of {outstanding(doc)}")
        if alloc.tds > 0 and payload.direction != "RECEIPT":
            raise HTTPException(status_code=422, detail="TDS deducted by the payer applies to receipts")
    det = {k: str(v).strip()[:120] for k, v in (payload.details or {}).items() if v not in (None, "") and k in ("cheque_no", "cheque_date", "bank_name", "branch", "account", "txn_ref", "tds", "narration")}
    if payload.method == "Cheque" and not (det.get("cheque_no") and det.get("cheque_date")):
        raise HTTPException(status_code=422, detail="A cheque needs its cheque number and cheque date")
    if payload.method == "Demand Draft" and not det.get("cheque_no"):
        raise HTTPException(status_code=422, detail="A demand draft needs its number")
    if payload.method in ("NEFT", "RTGS", "IMPS", "UPI", "Card") and not (payload.reference or det.get("txn_ref")):
        raise HTTPException(status_code=422, detail=f"A {payload.method} payment needs its transaction / UTR reference")
    full = bool(payload.allocations) and all(a.amount + a.tds + a.round_off == outstanding(docs[a.document_id]) for a in payload.allocations) and total_alloc == payload.amount
    if payload.kind == "full" and not full:
        raise HTTPException(status_code=422, detail="A full payment settles the whole balance of every selected document. Choose part payment, or allocate the full outstanding amounts")
    det["settlement"] = "FULL" if full else "PART"
    tds_total = sum((a.tds for a in payload.allocations), Decimal(0))
    if tds_total > 0:
        det["tds_total"] = str(q2(tds_total))
    pay_date = payload.pay_date or date.today()
    payment = Payment(details=det, organization_id=org_id, direction=payload.direction, pay_number=next_number(db, org_id, "receipt" if payload.direction == "RECEIPT" else "payment", PAYMENT_PREFIX[payload.direction], pay_date, user=user), pay_date=pay_date,
                      party_name=payload.party_name.strip(), method=payload.method, reference=payload.reference, amount=payload.amount, status="POSTED", idempotency_key=idempotency_key, remarks=payload.remarks, created_by=user.id)
    for alloc in payload.allocations:
        payment.allocations.append(PaymentAllocation(document_id=alloc.document_id, amount=alloc.amount, tds_amount=alloc.tds, round_off=alloc.round_off))
        docs[alloc.document_id].paid_amount += alloc.amount + alloc.tds + alloc.round_off
    db.add(payment)
    try:
        db.flush()
        gl.sync_payment(db, payment, user.id)
        record_audit(db, "PAYMENT_RECORDED", "BILLING", user, org_id, "Payment", str(payment.id), {"number": payment.pay_number, "direction": payment.direction, "amount": str(payment.amount), "allocations": len(payload.allocations)})
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id, Payment.idempotency_key == idempotency_key))
            if existing:
                return _out(existing)
        raise HTTPException(status_code=409, detail="Could not record the payment; please retry")
    return _out(_get(db, org_id, payment.id))


@router.get("/{payment_id}")
def get_payment(payment_id: int, user: User = Depends(permission_required("payment.read")), db: Session = Depends(get_db)):
    return _out(_get(db, _tenant(user), payment_id))


@router.get("/{payment_id}/voucher")
def payment_voucher(payment_id: int, user: User = Depends(permission_required("payment.read")), db: Session = Depends(get_db)):
    """Everything the receipt / payment voucher prints: the transaction details and, for each document it settles, its total, what this voucher applied and the balance that remained."""
    org_id = _tenant(user)
    p = _get(db, org_id, payment_id)
    lines = []
    for a in sorted(p.allocations, key=lambda x: x.id):
        doc = db.get(Document, a.document_id)
        before = db.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount + func.coalesce(PaymentAllocation.tds_amount, 0) + func.coalesce(PaymentAllocation.round_off, 0)), 0)).join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.document_id == a.document_id, Payment.status == "POSTED", PaymentAllocation.id <= a.id)) or Decimal(0)
        credited = doc.credited_amount if doc else Decimal(0)
        lines.append({"document_id": a.document_id, "doc_number": doc.doc_number if doc else None, "doc_type": doc.doc_type if doc else None, "doc_date": doc.doc_date if doc else None, "job_number": doc.job_number if doc else None,
                      "total": str(doc.grand_total) if doc else "0", "applied": str(a.amount), "tds": str(a.tds_amount or 0), "round_off": str(a.round_off or 0), "balance_after": str((doc.grand_total if doc else Decimal(0)) - Decimal(before) - credited)})
    return {**_out(p), "lines": lines}


@router.post("/{payment_id}/cancel")
def cancel_payment(payment_id: int, user: User = Depends(permission_required("payment.cancel")), db: Session = Depends(get_db)):
    """Cancel a payment and release its allocations (the payment record itself is kept for audit)."""
    org_id = _tenant(user)
    payment = _get(db, org_id, payment_id)
    if payment.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Payment is already cancelled")
    ids = sorted(a.document_id for a in payment.allocations)
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(ids)).order_by(Document.id).with_for_update())} if ids else {}
    for alloc in payment.allocations:
        doc = docs[alloc.document_id]
        doc.paid_amount = max(Decimal(0), doc.paid_amount - alloc.amount - (alloc.tds_amount or Decimal(0)) - (alloc.round_off or Decimal(0)))
    payment.status = "CANCELLED"
    gl.sync_payment(db, payment, user.id)
    record_audit(db, "PAYMENT_CANCELLED", "BILLING", user, org_id, "Payment", str(payment.id), {"number": payment.pay_number})
    db.commit()
    return _out(_get(db, org_id, payment_id))
