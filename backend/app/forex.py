"""Foreign currency documents: the original rate is kept, a settlement at another rate gives a realised forex gain / loss.

An overseas invoice (one foreign currency, no GST) is carried in rupees at its invoice rate. A receipt (or payment) against it states the foreign
amount settled and the rate of the day; the rupee value of the invoice that is cleared is foreign amount x invoice rate, the cash is foreign amount x
settlement rate, and the difference is the realised gain (positive) or loss (negative)."""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import outstanding, q2
from .models import Document, Payment, PaymentAllocation

ZERO = Decimal(0)


def doc_fx(doc: Document) -> dict | None:
    """(currency, foreign total, rupee total, invoice rate) of a single-foreign-currency document, else None."""
    lines = list(doc.lines or [])
    ccys = {l.ccy for l in lines}
    if not lines or len(ccys) != 1 or "INR" in ccys:
        return None
    fc = q2(sum((l.qty * l.rate for l in lines), ZERO))
    inr = q2(doc.grand_total)
    if fc <= 0 or inr <= 0:
        return None
    return {"ccy": next(iter(ccys)), "fc_total": fc, "inr_total": inr, "rate": (inr / fc)}


def fc_settled(db: Session, doc_id: int) -> Decimal:
    """Foreign amount already settled on the document by posted receipts / payments."""
    return q2(db.scalar(select(func.coalesce(func.sum(PaymentAllocation.fx_amount), 0)).join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.document_id == doc_id, Payment.status == "POSTED")) or ZERO)


def balance(db: Session, doc: Document) -> dict | None:
    fx = doc_fx(doc)
    if not fx:
        return None
    done = fc_settled(db, doc.id) + q2((doc.credited_amount or ZERO) / fx["rate"])
    return {**fx, "rate": str(round(fx["rate"], 6)), "fc_total": str(fx["fc_total"]), "inr_total": str(fx["inr_total"]), "fc_settled": str(done), "fc_balance": str(q2(fx["fc_total"] - done))}


def split(db: Session, doc: Document, fx_amount: Decimal, fx_rate: Decimal, receipt: bool) -> dict:
    """Book value cleared (rupees at the invoice rate), cash (rupees at the settlement rate) and the realised gain (+) / loss (-)."""
    fx = doc_fx(doc)
    if not fx:
        raise HTTPException(422, f"{doc.doc_number or doc.id} is not a single foreign currency document: settle it in rupees")
    if fx_rate <= 0:
        raise HTTPException(422, "Enter the exchange rate of the settlement")
    left = q2(fx["fc_total"] - fc_settled(db, doc.id) - (doc.credited_amount or ZERO) / fx["rate"])
    if fx_amount > left:
        raise HTTPException(422, f"The foreign amount {fx_amount} exceeds the balance of {left} {fx['ccy']} on {doc.doc_number or doc.id}")
    book = outstanding(doc) if fx_amount == left else q2(fx_amount * fx["rate"])      # the last settlement clears the exact rupee balance (no rounding leftover)
    cash = q2(fx_amount * fx_rate)
    gain = q2(cash - book) if receipt else q2(book - cash)
    return {"book": book, "cash": cash, "gain": gain, "ccy": fx["ccy"]}
