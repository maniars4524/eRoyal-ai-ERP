"""Forex revaluation of open foreign currency invoices at a reporting date, and the forex reports.

Revaluation: every unpaid / partly paid foreign currency invoice (AR) or purchase bill (AP) is carried at foreign balance x closing rate; the difference to the
carrying value is an unrealised gain / loss, posted as an ordinary Journal Voucher (party ledger against the Unrealised Forex Gain / Loss ledger). The journal is
reversed automatically the next day (so the books are back at the original value and the realised gain / loss at settlement is exact) and an invoice is never
revalued twice for the same date."""
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import coa, forex, gl
from .auth import permission_required
from .auth_api import record_audit
from .billing import fiscal_year, q2
from .db import get_db
from .models import AccEntry, AccVoucher, Customer, Document, ForexRevaluation, HblCounter, Payment, PaymentAllocation, User

router = APIRouter(prefix="/api/forex", tags=["forex"])
ZERO = Decimal(0)
AR_TYPES, AP_TYPES = ("invoice", "debit_note"), ("purchase_invoice",)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Forex is tenant-scoped; Super Admin does not manage it")
    return user.organization_id


def _as_of_state(db: Session, doc: Document, as_of: date) -> dict | None:
    """Foreign balance and rupee carrying value of an open foreign document on a date (payments up to that date only)."""
    fx = forex.doc_fx(doc)
    if not fx or doc.doc_date > as_of:
        return None
    rows = list(db.execute(select(PaymentAllocation).join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.document_id == doc.id, Payment.status == "POSTED", Payment.pay_date <= as_of)).scalars())
    fc_paid = sum((a.fx_amount or ZERO for a in rows), ZERO)
    book_paid = sum((a.amount + (a.tds_amount or ZERO) + (a.round_off or ZERO) for a in rows), ZERO)
    credited = _credited_by(db, doc, as_of)
    fc_bal = q2(fx["fc_total"] - fc_paid - credited / fx["rate"])
    carrying = q2(doc.grand_total - book_paid - credited)
    if fc_bal <= Decimal("0.005") or carrying <= 0:
        return None
    return {"ccy": fx["ccy"], "fc_balance": fc_bal, "carrying": carrying, "invoice_rate": fx["rate"]}


def _credited_by(db: Session, doc: Document, as_of: date) -> Decimal:
    """Credit notes against the document up to the date (by the date of each credit note); a document credited without a linked note keeps its recorded amount."""
    notes = list(db.scalars(select(Document).where(Document.ref_document_id == doc.id, Document.status == "POSTED", Document.doc_type.in_(("credit_note", "purchase_credit_note")))))
    if not notes:
        return doc.credited_amount or ZERO
    return sum((n.grand_total for n in notes if n.doc_date <= as_of), ZERO)


def open_foreign(db: Session, org_id: int, as_of: date) -> list[dict]:
    out = []
    q = select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(AR_TYPES + AP_TYPES), Document.doc_date <= as_of).order_by(Document.doc_date, Document.id)
    for d in db.scalars(q):
        st = _as_of_state(db, d, as_of)
        if st:
            out.append({"document": d, "recv": d.doc_type in AR_TYPES, **st})
    return out


@router.get("/open")
def open_documents(as_of: date, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """The open foreign currency invoices / bills at a date, with the currencies that need a closing rate."""
    org_id = _tenant(user)
    rows = open_foreign(db, org_id, as_of)
    done = {r.document_id for r in db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.as_of == as_of, ForexRevaluation.status == "ACTIVE"))}
    return {"as_of": as_of, "currencies": sorted({r["ccy"] for r in rows}), "items": [{"document_id": r["document"].id, "doc_number": r["document"].doc_number, "party": r["document"].party_name, "type": r["document"].doc_type, "ccy": r["ccy"], "fc_balance": str(r["fc_balance"]),
            "carrying": str(r["carrying"]), "invoice_rate": str(round(r["invoice_rate"], 6)), "already_revalued": r["document"].id in done} for r in rows]}


class RevalueIn(BaseModel):
    as_of: date
    rates: dict[str, Decimal] = Field(default_factory=dict)      # closing rate per currency: INR for 1 unit


def _next_jv(db: Session, org_id: int, vdate: date) -> str:
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == org_id, HblCounter.hbl_type == "JV").with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=org_id, hbl_type="JV", last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    return f"JV/{fiscal_year(vdate)}/{ctr.last_no:05d}"


@router.post("/revalue", status_code=201)
def revalue(body: RevalueIn, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    return _revalue(db, _tenant(user), body, user)


def _revalue(db: Session, org_id: int, body: RevalueIn, user: User, commit: bool = True) -> dict:
    gl.assert_open(db, org_id, body.as_of)
    gl.assert_open(db, org_id, body.as_of + timedelta(days=1))
    coa.ensure_tree(db, org_id)
    items = open_foreign(db, org_id, body.as_of)
    if not items:
        raise HTTPException(422, "There is no open foreign currency invoice or bill on that date")
    need = sorted({r["ccy"] for r in items} - {c.upper() for c, v in body.rates.items() if v and v > 0})
    if need:
        raise HTTPException(422, "Enter the closing rate of " + ", ".join(need))
    rates = {c.upper(): v for c, v in body.rates.items()}
    done = {r.document_id for r in db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.as_of == body.as_of, ForexRevaluation.status == "ACTIVE"))}
    g_u, l_u = coa.sys_ledger(db, org_id, "fx_gain_unreal"), coa.sys_ledger(db, org_id, "fx_loss_unreal")
    dr_rows, cr_rows, recs, skipped = [], [], [], 0
    for r in items:
        d = r["document"]
        if d.id in done:
            skipped += 1                                                       # never revalued twice for the same date
            continue
        rate = rates[r["ccy"]]
        revalued = q2(r["fc_balance"] * rate)
        diff = q2(revalued - r["carrying"])                                    # receivable: more rupees = gain; payable: more rupees = loss
        gain = diff if r["recv"] else -diff
        cust = db.get(Customer, d.customer_id) if d.customer_id else None
        party = coa.party_ledger(db, org_id, d.party_name, "debtor" if r["recv"] else "creditor", cust if cust is not None and cust.name == d.party_name else None, user.id)
        recs.append(ForexRevaluation(organization_id=org_id, document_id=d.id, as_of=body.as_of, ccy=r["ccy"], fc_balance=r["fc_balance"], closing_rate=rate, carrying_inr=r["carrying"], revalued_inr=revalued, difference=gain, status="ACTIVE", created_by=user.id))
        if gain == 0:
            continue
        n = f"Revaluation {d.doc_number or d.id} {r['fc_balance']} {r['ccy']} @ {rate}"
        move = diff                                                            # change of the party balance in rupees
        # receivable: Dr party for an increase; payable: Cr party for an increase
        if r["recv"]:
            dr_rows.append((party, move, ZERO, n, d.job_number)) if move > 0 else dr_rows.append((party, ZERO, -move, n, d.job_number))
        else:
            dr_rows.append((party, ZERO, move, n, d.job_number)) if move > 0 else dr_rows.append((party, -move, ZERO, n, d.job_number))
        cr_rows.append((g_u, ZERO, gain, n, d.job_number) if gain > 0 else (l_u, -gain, ZERO, n, d.job_number))
    if not recs:
        raise HTTPException(409, "Those invoices are already revalued for that date")
    for x in recs:
        db.add(x)
    db.flush()
    vdate, rdate = body.as_of, body.as_of + timedelta(days=1)
    rows = dr_rows + cr_rows
    v = gl._voucher(db, org_id, "JOURNAL", _next_jv(db, org_id, vdate), vdate, rows, source_type="forex_reval", source_id=recs[0].id, narration=f"Forex revaluation of open foreign currency balances at {vdate.strftime('%d-%m-%Y')}", user_id=user.id) if rows else None
    rev = gl._voucher(db, org_id, "JOURNAL", _next_jv(db, org_id, rdate), rdate, [(l, c, d_, n, j) for (l, d_, c, n, j) in rows], source_type="forex_reval_rev", source_id=recs[0].id, narration=f"Reversal of the forex revaluation of {vdate.strftime('%d-%m-%Y')}", user_id=user.id) if rows else None
    for x in recs:
        x.voucher_id, x.reversal_voucher_id = (v.id if v else None), (rev.id if rev else None)
    record_audit(db, "FOREX_REVALUED", "ACCOUNTS", user, org_id, "ForexRevaluation", str(recs[0].id), {"as_of": str(vdate), "documents": len(recs), "skipped": skipped, "net": str(sum((x.difference for x in recs), ZERO))})
    if commit:
        db.commit()
    return {"as_of": vdate, "revalued": len(recs), "skipped": skipped, "unrealised_net": str(sum((x.difference for x in recs), ZERO)), "voucher": v.number if v else None, "reversal_voucher": rev.number if rev else None, "reversal_date": rdate}


@router.delete("/revaluations/{as_of}")
def cancel_revaluation(as_of: date, user: User = Depends(permission_required("billing.cancel")), db: Session = Depends(get_db)):
    """Undo the revaluation of a date (its journal and the reversal), for example to run it again with another closing rate."""
    return _cancel(db, _tenant(user), as_of, user)


def refresh_for_documents(db: Session, org_id: int, doc_ids: list[int], pay_date: date, user: User) -> list[date]:
    """A receipt / payment dated on or before a revaluation date changes the balances that date was valued on: those revaluations are made again with the same closing rates."""
    dates = sorted({r.as_of for r in db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.status == "ACTIVE", ForexRevaluation.document_id.in_(doc_ids), ForexRevaluation.as_of >= pay_date))})
    done = []
    for d in dates:
        rates = {r.ccy: r.closing_rate for r in db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.status == "ACTIVE", ForexRevaluation.as_of == d))}
        try:
            _cancel(db, org_id, d, user, commit=False)
            db.flush()
            try:
                _revalue(db, org_id, RevalueIn(as_of=d, rates=rates), user, commit=False)
            except HTTPException:
                pass                                                          # nothing is open any more on that date: the revaluation simply ends
            done.append(d)
        except HTTPException:
            continue                                                          # a closed period is left as it was
    return done


def _cancel(db: Session, org_id: int, as_of: date, user: User, commit: bool = True) -> dict:
    gl.assert_open(db, org_id, as_of)
    rows = list(db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.as_of == as_of, ForexRevaluation.status == "ACTIVE")))
    if not rows:
        raise HTTPException(404, "No revaluation on that date")
    ids = {x for r in rows for x in (r.voucher_id, r.reversal_voucher_id) if x}
    for vid in ids:
        v = db.get(AccVoucher, vid)
        if v:
            db.query(AccEntry).filter(AccEntry.voucher_id == vid).delete()
            db.delete(v)
    for r in rows:
        db.delete(r)
    record_audit(db, "FOREX_REVALUATION_CANCELLED", "ACCOUNTS", user, org_id, "ForexRevaluation", str(as_of), {"documents": len(rows)})
    if commit:
        db.commit()
    return {"cancelled": len(rows)}


@router.get("/revaluations")
def history(user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Revaluation history, invoice by invoice."""
    org_id = _tenant(user)
    out = []
    for r in db.scalars(select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id).order_by(ForexRevaluation.as_of.desc(), ForexRevaluation.id)):
        d = db.get(Document, r.document_id)
        out.append({"as_of": r.as_of, "document": d.doc_number if d else r.document_id, "party": d.party_name if d else "", "ccy": r.ccy, "fc_balance": str(r.fc_balance), "closing_rate": str(r.closing_rate), "carrying_inr": str(r.carrying_inr), "revalued_inr": str(r.revalued_inr), "unrealised": str(r.difference), "status": r.status})
    return out


# ---------------------------------------------------------------- reports
@router.get("/reports/realised")
def report_realised(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Realised forex gain / loss: every foreign currency settlement with the invoice rate, the settlement rate and the difference."""
    org_id = _tenant(user)
    q = select(PaymentAllocation, Payment, Document).join(Payment, Payment.id == PaymentAllocation.payment_id).join(Document, Document.id == PaymentAllocation.document_id).where(Payment.organization_id == org_id, Payment.status == "POSTED", PaymentAllocation.fx_amount.is_not(None)).order_by(Payment.pay_date, Payment.id)
    if date_from:
        q = q.where(Payment.pay_date >= date_from)
    if date_to:
        q = q.where(Payment.pay_date <= date_to)
    rows, gain, loss = [], ZERO, ZERO
    for a, p, d in db.execute(q):
        fx = forex.doc_fx(d)
        g = a.fx_gain or ZERO
        gain += g if g > 0 else ZERO
        loss += -g if g < 0 else ZERO
        rows.append({"date": p.pay_date, "voucher": p.pay_number, "kind": p.direction, "party": d.party_name, "document": d.doc_number, "ccy": fx["ccy"] if fx else "", "fx_amount": str(a.fx_amount), "invoice_rate": str(round(fx["rate"], 4)) if fx else "", "settlement_rate": str(a.fx_rate),
                     "book_inr": str(a.amount), "cash_inr": str(a.fx_cash), "gain_loss": str(g)})
    return {"rows": rows, "gain": str(gain), "loss": str(loss), "net": str(gain - loss)}


@router.get("/reports/unrealised")
def report_unrealised(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Unrealised forex gain / loss of the revaluations in the period (invoice-wise)."""
    org_id = _tenant(user)
    q = select(ForexRevaluation).where(ForexRevaluation.organization_id == org_id, ForexRevaluation.status == "ACTIVE").order_by(ForexRevaluation.as_of, ForexRevaluation.id)
    if date_from:
        q = q.where(ForexRevaluation.as_of >= date_from)
    if date_to:
        q = q.where(ForexRevaluation.as_of <= date_to)
    rows, gain, loss = [], ZERO, ZERO
    for r in db.scalars(q):
        d = db.get(Document, r.document_id)
        gain += r.difference if r.difference > 0 else ZERO
        loss += -r.difference if r.difference < 0 else ZERO
        rows.append({"as_of": r.as_of, "party": d.party_name if d else "", "document": d.doc_number if d else r.document_id, "ccy": r.ccy, "fc_balance": str(r.fc_balance), "closing_rate": str(r.closing_rate), "carrying_inr": str(r.carrying_inr), "revalued_inr": str(r.revalued_inr), "gain_loss": str(r.difference)})
    return {"rows": rows, "gain": str(gain), "loss": str(loss), "net": str(gain - loss)}


@router.get("/reports/invoicewise")
def report_invoicewise(date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Invoice-wise exchange rate difference: the invoice rate, what was settled and the realised difference, the latest unrealised difference and the balance."""
    org_id = _tenant(user)
    date_to = date_to or date.today()
    rows = []
    for d in db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(AR_TYPES + AP_TYPES), Document.doc_date <= date_to).order_by(Document.doc_date, Document.id)):
        fx = forex.doc_fx(d)
        if not fx:
            continue
        al = list(db.execute(select(PaymentAllocation).join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.document_id == d.id, Payment.status == "POSTED", Payment.pay_date <= date_to)).scalars())
        realised = sum((a.fx_gain or ZERO for a in al), ZERO)
        last = db.scalar(select(ForexRevaluation).where(ForexRevaluation.document_id == d.id, ForexRevaluation.status == "ACTIVE", ForexRevaluation.as_of <= date_to).order_by(ForexRevaluation.as_of.desc()).limit(1))
        st = _as_of_state(db, d, date_to)
        rows.append({"document": d.doc_number, "date": d.doc_date, "party": d.party_name, "type": d.doc_type, "ccy": fx["ccy"], "fc_total": str(fx["fc_total"]), "invoice_rate": str(round(fx["rate"], 4)), "inr_total": str(fx["inr_total"]), "fc_settled": str(sum((a.fx_amount or ZERO for a in al), ZERO)),
                     "realised": str(realised), "unrealised": str(last.difference if last else ZERO), "fc_balance": str(st["fc_balance"] if st else ZERO)})
    return {"rows": rows}


@router.get("/reports/outstanding")
def report_outstanding(as_of: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Outstanding foreign currency receivables and payables."""
    org_id = _tenant(user)
    as_of = as_of or date.today()
    rows = [{"kind": "Receivable" if r["recv"] else "Payable", "party": r["document"].party_name, "document": r["document"].doc_number, "date": r["document"].doc_date, "ccy": r["ccy"], "fc_balance": str(r["fc_balance"]), "invoice_rate": str(round(r["invoice_rate"], 4)), "carrying_inr": str(r["carrying"])} for r in open_foreign(db, org_id, as_of)]
    return {"as_of": as_of, "rows": rows}


@router.get("/reports/reconciliation")
def report_reconciliation(as_of: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Customer / vendor wise reconciliation: foreign invoiced - settled - credited = balance, and the rupee carrying value against balance x invoice rate."""
    org_id = _tenant(user)
    as_of = as_of or date.today()
    agg: dict = {}
    for d in db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(AR_TYPES + AP_TYPES), Document.doc_date <= as_of)):
        fx = forex.doc_fx(d)
        if not fx:
            continue
        al = list(db.execute(select(PaymentAllocation).join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.document_id == d.id, Payment.status == "POSTED", Payment.pay_date <= as_of)).scalars())
        a = agg.setdefault((d.party_name, fx["ccy"], "Receivable" if d.doc_type in AR_TYPES else "Payable"), {"invoiced": ZERO, "settled": ZERO, "credited": ZERO, "inr_invoiced": ZERO, "inr_book_paid": ZERO, "inr_credited": ZERO, "realised": ZERO})
        a["invoiced"] += fx["fc_total"]
        a["settled"] += sum((x.fx_amount or ZERO for x in al), ZERO)
        a["credited"] += q2((d.credited_amount or ZERO) / fx["rate"])
        a["inr_invoiced"] += fx["inr_total"]
        a["inr_book_paid"] += sum((x.amount + (x.tds_amount or ZERO) + (x.round_off or ZERO) for x in al), ZERO)
        a["inr_credited"] += d.credited_amount or ZERO
        a["realised"] += sum((x.fx_gain or ZERO for x in al), ZERO)
    rows = []
    for (party, ccy, kind), a in sorted(agg.items()):
        bal = a["invoiced"] - a["settled"] - a["credited"]
        book = a["inr_invoiced"] - a["inr_book_paid"] - a["inr_credited"]
        rows.append({"party": party, "kind": kind, "ccy": ccy, "invoiced": str(a["invoiced"]), "settled": str(a["settled"]), "credited": str(a["credited"]), "balance": str(bal), "book_inr": str(book), "realised": str(a["realised"]), "reconciled": (abs(bal) < Decimal("0.01")) == (abs(book) < Decimal("0.05"))})            # a nil foreign balance means a nil rupee balance, and the other way round
    return {"as_of": as_of, "rows": rows}
