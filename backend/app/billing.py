"""Shared billing rules: fiscal-year numbering, line calculation, outstanding balances."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import DocSequence, Document

TWO = Decimal("0.01")
# doc_type -> (number prefix, ledger side)
DOC_TYPES = {
    "invoice": ("INV", "AR"),
    "debit_note": ("DN", "AR"),
    "credit_note": ("CN", "AR"),
    "purchase_invoice": ("PI", "AP"),
    "purchase_credit_note": ("PCN", "AP"),
}
PAYABLE_TYPES = {"RECEIPT": {"invoice", "debit_note"}, "PAYMENT": {"purchase_invoice"}}
CREDIT_TARGET = {"credit_note": "invoice", "purchase_credit_note": "purchase_invoice"}
PAYMENT_PREFIX = {"RECEIPT": "RCP", "PAYMENT": "PAY"}


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def fiscal_year(on: date) -> str:
    """Indian financial year (April-March), e.g. 2026-05-01 -> '26-27'."""
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def next_number(db: Session, organization_id: int, seq_type: str, prefix: str, on: date) -> str:
    """Allocate the next gap-free number. The counter row is locked until the surrounding transaction ends."""
    fy = fiscal_year(on)
    db.execute(pg_insert(DocSequence).values(organization_id=organization_id, seq_type=seq_type, fiscal_year=fy, last_number=0).on_conflict_do_nothing(constraint="uq_doc_sequence"))
    seq = db.scalar(select(DocSequence).where(DocSequence.organization_id == organization_id, DocSequence.seq_type == seq_type, DocSequence.fiscal_year == fy).with_for_update())
    seq.last_number += 1
    db.flush()
    return f"{prefix}/{fy}/{seq.last_number:05d}"


def calc_line(qty: Decimal, ccy: str, exch: Decimal, rate: Decimal, tax_type: str, tax_pct: Decimal) -> dict:
    gross = qty * rate
    amount = gross if ccy == "INR" else gross * exch
    tax = amount * tax_pct / Decimal(100) if tax_type == "Taxable" else Decimal(0)
    amount, tax = q2(amount), q2(tax)
    return {"amount": amount, "tax_amount": tax, "line_total": amount + tax}


def outstanding(doc: Document) -> Decimal:
    return doc.grand_total - doc.paid_amount - doc.credited_amount


def payment_status(doc: Document) -> str:
    if doc.status != "POSTED":
        return doc.status
    if doc.doc_type in ("credit_note", "purchase_credit_note"):
        return "POSTED"
    left = outstanding(doc)
    if left <= 0:
        return "PAID"
    return "PARTIAL" if (doc.paid_amount + doc.credited_amount) > 0 else "UNPAID"


def doc_side(doc_type: str) -> str:
    if doc_type not in DOC_TYPES:
        raise HTTPException(status_code=422, detail="Unknown document type")
    return DOC_TYPES[doc_type][1]
