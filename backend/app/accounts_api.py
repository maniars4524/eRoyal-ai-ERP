"""Accounts reports built from the vouchers: party ledger, cash / bank book, outstanding by party and the job profit and loss.
Every invoice, note, receipt and payment of a job is the same record the job screens use, so these reports always agree with the job."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .billing import outstanding, q2
from .db import get_db
from .models import Document, JobCharge, Payment, Shipment, User

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
AR_TYPES = ("invoice", "debit_note", "credit_note")
AP_TYPES = ("purchase_invoice", "purchase_credit_note")
CREDITS = ("credit_note", "purchase_credit_note")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Accounts reports are tenant-scoped")
    return user.organization_id


def _s(v) -> str:
    return str(q2(Decimal(v or 0)))


@router.get("/party-ledger")
def party_ledger(party: str = Query(max_length=200), date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Chronological voucher list of one party with a running balance. Customer side: invoices and debit notes debit, credit notes and receipts credit.
    Vendor side: purchase invoices credit, purchase credit notes and payments debit. The closing balance is what the party owes us (positive) or what we owe (negative)."""
    org_id = _tenant(user)
    rows = []
    docs = db.scalars(select(Document).where(Document.organization_id == org_id, Document.party_name == party, Document.status == "POSTED").order_by(Document.doc_date, Document.id))
    for d in docs:
        ar = d.doc_type in AR_TYPES
        credit_side = d.doc_type in CREDITS
        amt = Decimal(d.grand_total)
        debit = amt if (ar and not credit_side) or (not ar and credit_side) else Decimal(0)
        credit = amt - debit
        rows.append({"date": d.doc_date, "voucher": d.doc_number, "type": d.doc_type, "job_number": d.job_number, "narration": d.remarks or "", "debit": debit, "credit": credit, "sort": (d.doc_date, 0, d.id)})
    for p in db.scalars(select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id, Payment.party_name == party, Payment.status == "POSTED").order_by(Payment.pay_date, Payment.id)):
        recv = p.direction == "RECEIPT"
        jobs = sorted({(db.get(Document, a.document_id).job_number or "") for a in p.allocations if db.get(Document, a.document_id)} - {""})
        tds = sum((a.tds_amount or Decimal(0) for a in p.allocations), Decimal(0))
        rows.append({"date": p.pay_date, "voucher": p.pay_number, "type": "receipt" if recv else "payment", "job_number": ", ".join(jobs), "narration": f"{p.method}" + (f" · {p.reference}" if p.reference else "") + (f" · incl. TDS {q2(tds)}" if tds else ""),
                     "debit": Decimal(0) if recv else p.amount, "credit": (p.amount + tds + sum((a.round_off or Decimal(0) for a in p.allocations), Decimal(0))) if recv else Decimal(0), "sort": (p.pay_date, 1, p.id)})
    rows.sort(key=lambda r: r["sort"])
    opening = sum((r["debit"] - r["credit"] for r in rows if date_from and r["date"] < date_from), Decimal(0))
    out, bal = [], opening
    for r in rows:
        if (date_from and r["date"] < date_from) or (date_to and r["date"] > date_to):
            continue
        bal += r["debit"] - r["credit"]
        out.append({k: (_s(v) if isinstance(v, Decimal) else v) for k, v in r.items() if k != "sort"} | {"balance": _s(bal)})
    return {"party": party, "opening": _s(opening), "entries": out, "total_debit": _s(sum((Decimal(r["debit"]) for r in out), Decimal(0))), "total_credit": _s(sum((Decimal(r["credit"]) for r in out), Decimal(0))), "closing": _s(bal)}


@router.get("/cash-book")
def cash_book(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("payment.read")), db: Session = Depends(get_db)):
    """Receipts and payments by date, split cash / bank (cheque, NEFT, RTGS, UPI ...), with the running balance."""
    org_id = _tenant(user)
    q = select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id, Payment.status == "POSTED")
    rows = list(db.scalars(q.order_by(Payment.pay_date, Payment.id)))
    opening = {"cash": Decimal(0), "bank": Decimal(0)}
    entries, bal = [], {"cash": Decimal(0), "bank": Decimal(0)}
    for p in rows:
        book = "cash" if p.method == "Cash" else "bank"
        signed = p.amount if p.direction == "RECEIPT" else -p.amount
        if date_from and p.pay_date < date_from:
            opening[book] += signed
            bal[book] += signed
            continue
        if date_to and p.pay_date > date_to:
            continue
        bal[book] += signed
        entries.append({"date": p.pay_date, "voucher": p.pay_number, "party": p.party_name, "book": book, "method": p.method, "reference": p.reference or (p.details or {}).get("cheque_no") or "", "account": (p.details or {}).get("account") or "",
                        "receipt": _s(p.amount) if p.direction == "RECEIPT" else "0.00", "payment": _s(p.amount) if p.direction == "PAYMENT" else "0.00", "balance": _s(bal[book])})
    return {"opening": {k: _s(v) for k, v in opening.items()}, "entries": entries, "closing": {k: _s(v) for k, v in bal.items()},
            "total_receipts": _s(sum((Decimal(e["receipt"]) for e in entries), Decimal(0))), "total_payments": _s(sum((Decimal(e["payment"]) for e in entries), Decimal(0)))}


@router.get("/outstanding")
def outstanding_report(side: str = Query(default="AR", pattern="^(AR|AP)$"), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """What each customer owes (AR) or what we owe each vendor (AP): posted documents less receipts / payments and credit notes, with ageing from the due date."""
    org_id = _tenant(user)
    types = ("invoice", "debit_note") if side == "AR" else ("purchase_invoice",)
    today = date.today()
    parties: dict[str, dict] = defaultdict(lambda: {"total": Decimal(0), "outstanding": Decimal(0), "current": Decimal(0), "d1_30": Decimal(0), "d31_60": Decimal(0), "d61_90": Decimal(0), "d90": Decimal(0), "documents": 0})
    for d in db.scalars(select(Document).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(types))):
        left = outstanding(d)
        if left <= 0:
            continue
        r = parties[d.party_name]
        late = (today - (d.due_date or d.doc_date)).days
        bucket = "current" if late <= 0 else "d1_30" if late <= 30 else "d31_60" if late <= 60 else "d61_90" if late <= 90 else "d90"
        r["total"] += d.grand_total
        r["outstanding"] += left
        r[bucket] += left
        r["documents"] += 1
    rows = [{"party": k, **{x: (_s(y) if isinstance(y, Decimal) else y) for x, y in v.items()}} for k, v in sorted(parties.items())]
    return {"side": side, "as_on": today, "parties": rows, "total_outstanding": _s(sum((v["outstanding"] for v in parties.values()), Decimal(0)))}


@router.get("/job/{job_number:path}/profit-loss")
def job_profit_loss(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """The job profit and loss: provisional figures from the job's charge sheet (sell and cost), actual figures from the posted invoices / purchase invoices (and the notes
    against them), tax on sale and on cost, revenue by customer, expense by vendor and what has been received and paid."""
    org_id = _tenant(user)
    job = db.scalar(select(Shipment).where(Shipment.organization_id == org_id, Shipment.job_number == job_number))
    if not job:
        raise HTTPException(404, "Job not found")
    charges = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == org_id).order_by(JobCharge.line_no)))
    from .models import DocumentLine

    line_docs = select(DocumentLine.document_id).where(DocumentLine.job_number == job_number)
    docs = list(db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED", (Document.job_number == job_number) | Document.id.in_(line_docs))))
    rows: dict[str, dict] = {}

    def row(name: str) -> dict:
        return rows.setdefault(name, {"charge": name, "basis": "", "qty": "", "p_sale": Decimal(0), "p_cost": Decimal(0), "a_sale": Decimal(0), "a_cost": Decimal(0)})

    sale_tax = cost_tax = Decimal(0)
    revenue: dict[str, Decimal] = defaultdict(Decimal)
    expense: list[dict] = []
    for c in charges:
        r = row(c.charge_name)
        qty = Decimal(c.qty or 0)
        pct = c.sell_pct if c.sell_pct is not None else c.buy_pct
        r["basis"] = r["basis"] or c.uom                                                       # the Basis and quantity the charge was entered on (a percentage line shows its percentage)
        r["qty"] = (f"{Decimal(pct).normalize():f}%" if pct is not None else str((Decimal(r["qty"] or 0) + qty).normalize()))
        sell = qty * Decimal(c.sell_rate or 0) * (1 if c.sell_ccy == "INR" else Decimal(c.sell_exch or 1))
        buy = qty * Decimal(c.buy_rate or 0) * (1 if c.buy_ccy == "INR" else Decimal(c.buy_exch or 1))
        r["p_sale"] += sell
        r["p_cost"] += buy
        if c.sell_tax_type == "Taxable":
            sale_tax += sell * Decimal(c.sell_tax_pct or 0) / 100
        if c.buy_tax_type == "Taxable":
            cost_tax += buy * Decimal(c.buy_tax_pct or 0) / 100
        if sell > 0:
            revenue[c.client or "—"] += sell
        if buy > 0:
            expense.append({"vendor": c.vendor or "—", "charge": c.charge_name, "p_cost": _s(buy)})
    received = paid = Decimal(0)
    for d in docs:
        sign = -1 if d.doc_type in CREDITS else 1
        mine = [l for l in d.lines if (l.job_number or d.job_number) == job_number]
        share = (sum((Decimal(l.amount) for l in mine), Decimal(0)) / sum((Decimal(l.amount) for l in d.lines), Decimal(0))) if d.lines and sum((Decimal(l.amount) for l in d.lines), Decimal(0)) else Decimal(1)
        if d.doc_type in ("invoice", "debit_note", "credit_note"):
            for l in mine:
                row(l.charge_name)["a_sale"] += sign * Decimal(l.amount)
            received += (d.paid_amount * share) if d.doc_type in ("invoice", "debit_note") else 0
        elif d.doc_type in AP_TYPES:
            for l in mine:
                row(l.charge_name)["a_cost"] += sign * Decimal(l.amount)
            paid += (d.paid_amount * share) if d.doc_type == "purchase_invoice" else 0
    lines = []
    tot = {"p_sale": Decimal(0), "p_cost": Decimal(0), "a_sale": Decimal(0), "a_cost": Decimal(0)}
    for r in rows.values():
        for k in tot:
            tot[k] += r[k]
        lines.append({"charge": r["charge"], "basis": r.get("basis", ""), "qty": r.get("qty", ""), "p_sale": _s(r["p_sale"]), "p_cost": _s(r["p_cost"]), "p_gp": _s(r["p_sale"] - r["p_cost"]), "a_sale": _s(r["a_sale"]), "a_cost": _s(r["a_cost"]), "a_gp": _s(r["a_sale"] - r["a_cost"])})
    p_gp, a_gp = tot["p_sale"] - tot["p_cost"], tot["a_sale"] - tot["a_cost"]
    return {"job_number": job.job_number, "lines": lines, "totals": {"p_sale": _s(tot["p_sale"]), "p_cost": _s(tot["p_cost"]), "p_gp": _s(p_gp), "a_sale": _s(tot["a_sale"]), "a_cost": _s(tot["a_cost"]), "a_gp": _s(a_gp)},
            "provisional_pct": _s(p_gp * 100 / tot["p_sale"]) if tot["p_sale"] else "0.00", "actual_pct": _s(a_gp * 100 / tot["a_sale"]) if tot["a_sale"] else "0.00",
            "tax": {"sale": _s(sale_tax), "cost": _s(cost_tax), "net": _s(sale_tax - cost_tax)}, "revenue": [{"bill_to": k, "p_sale": _s(v)} for k, v in revenue.items()], "expense": expense,
            "cash": {"received": _s(received), "paid": _s(paid), "net": _s(received - paid)}, "invoiced": _s(sum((Decimal(d.grand_total) * (sum((Decimal(l.amount) for l in d.lines if (l.job_number or d.job_number) == job_number), Decimal(0)) / (sum((Decimal(l.amount) for l in d.lines), Decimal(0)) or Decimal(1))) for d in docs if d.doc_type in ("invoice", "debit_note")), Decimal(0)))}
