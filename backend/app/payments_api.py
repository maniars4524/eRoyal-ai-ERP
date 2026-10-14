from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .auth_api import record_audit
from .billing import PAYABLE_TYPES, PAYMENT_PREFIX, next_number, outstanding
from .db import get_db
from .models import Document, Payment, PaymentAllocation, User

router = APIRouter(prefix="/api/payments", tags=["payments"])


class Allocation(BaseModel):
    document_id: int
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)


class PaymentIn(BaseModel):
    direction: str = Field(pattern="^(RECEIPT|PAYMENT)$")
    pay_date: date | None = None
    party_name: str = Field(default="", max_length=200)
    method: str = Field(default="Bank Transfer", max_length=30)
    reference: str | None = Field(default=None, max_length=80)
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
            "allocated": str(allocated), "unallocated": str(p.amount - allocated), "remarks": p.remarks, "created_at": p.created_at,
            "allocations": [{"document_id": a.document_id, "amount": str(a.amount)} for a in p.allocations]}


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
        if alloc.amount > outstanding(doc):
            raise HTTPException(status_code=422, detail=f"Allocation to {doc.doc_number} exceeds its outstanding balance of {outstanding(doc)}")
    pay_date = payload.pay_date or date.today()
    payment = Payment(organization_id=org_id, direction=payload.direction, pay_number=next_number(db, org_id, "receipt" if payload.direction == "RECEIPT" else "payment", PAYMENT_PREFIX[payload.direction], pay_date, user=user), pay_date=pay_date,
                      party_name=payload.party_name.strip(), method=payload.method, reference=payload.reference, amount=payload.amount, status="POSTED", idempotency_key=idempotency_key, remarks=payload.remarks, created_by=user.id)
    for alloc in payload.allocations:
        payment.allocations.append(PaymentAllocation(document_id=alloc.document_id, amount=alloc.amount))
        docs[alloc.document_id].paid_amount += alloc.amount
    db.add(payment)
    try:
        db.flush()
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
        doc.paid_amount = max(Decimal(0), doc.paid_amount - alloc.amount)
    payment.status = "CANCELLED"
    record_audit(db, "PAYMENT_CANCELLED", "BILLING", user, org_id, "Payment", str(payment.id), {"number": payment.pay_number})
    db.commit()
    return _out(_get(db, org_id, payment_id))
