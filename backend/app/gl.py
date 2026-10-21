"""General ledger posting. Every posted document and payment mirrors itself as a balanced voucher of ledger entries; cancelling removes it.

Sales invoice / debit note : Dr party (debtor)            Cr income ledger per charge, Output CGST+SGST or IGST, Pure Agent reimbursement, round off
Credit note                : the same, reversed
Purchase invoice           : Dr expense ledger per charge, Input GST, round off     Cr party (creditor)
Purchase credit note       : reversed
Receipt                    : Dr bank / cash (money), TDS Receivable, round off      Cr party (debtor) for money + TDS + round off
Payment                    : Dr party (creditor) for money + TDS + round off        Cr bank / cash, TDS Payable, round off
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from datetime import date

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from . import coa
from .models import AccEntry, AccVoucher, Customer, Document, Payment

ZERO = Decimal(0)
DOC_VOUCHER = {"invoice": "SALES", "debit_note": "DEBIT_NOTE", "credit_note": "CREDIT_NOTE", "purchase_invoice": "PURCHASE", "purchase_credit_note": "PURCHASE_CREDIT_NOTE"}
VOUCHER_LABEL = {"SALES": "Sales Invoice", "DEBIT_NOTE": "Debit Note", "CREDIT_NOTE": "Credit Note", "PURCHASE": "Purchase Invoice", "PURCHASE_CREDIT_NOTE": "Purchase Credit Note", "CLOSING": "Year-end Closing", "RECEIPT": "Receipt Voucher",
                 "PAYMENT": "Payment Voucher", "JOURNAL": "Journal Voucher", "CONTRA": "Contra Voucher"}


def locked_until(db: Session, org_id: int) -> date | None:
    """The last day of the latest closed financial year: nothing dated on or before it can be posted, changed or cancelled."""
    from .models import FyClosing

    return db.scalar(select(func.max(FyClosing.end_date)).where(FyClosing.organization_id == org_id))


def assert_open(db: Session, org_id: int, d: date | None) -> None:
    lock = locked_until(db, org_id)
    if d is not None and lock is not None and d <= lock:
        raise HTTPException(409, f"The financial year up to {lock.strftime('%d-%m-%Y')} is closed, so nothing dated {d.strftime('%d-%m-%Y')} can be posted, changed or cancelled. Reopen the year in Ledger Reports > Financial Year to change it")


def q2(v) -> Decimal:
    return Decimal(v or 0).quantize(Decimal("0.01"))


def _drop(db: Session, org_id: int, source_type: str, source_id: int) -> None:
    ids = list(db.scalars(select(AccVoucher.id).where(AccVoucher.organization_id == org_id, AccVoucher.source_type == source_type, AccVoucher.source_id == source_id)))
    if ids:
        db.execute(delete(AccEntry).where(AccEntry.voucher_id.in_(ids)))
        db.execute(delete(AccVoucher).where(AccVoucher.id.in_(ids)))
        db.flush()


def _voucher(db: Session, org_id: int, vtype: str, number: str, vdate, rows: list, *, source_type=None, source_id=None, narration=None, party=None, job=None, user_id=None) -> AccVoucher:
    """rows: (ledger, debit, credit, narration, job). Any rounding difference is carried by the Round Off ledger so the voucher always balances."""
    rows = [(l, q2(d), q2(c), n, j) for l, d, c, n, j in rows if q2(d) or q2(c)]
    diff = sum((d for _, d, _, _, _ in rows), ZERO) - sum((c for _, _, c, _, _ in rows), ZERO)
    if diff:
        ro = coa.sys_ledger(db, org_id, "round_off")
        rows.append((ro, ZERO, diff, "Round off", None) if diff > 0 else (ro, -diff, ZERO, "Round off", None))
    v = AccVoucher(organization_id=org_id, voucher_type=vtype, number=number, vdate=vdate, narration=narration, party_name=party, job_number=job, source_type=source_type, source_id=source_id,
                   total=sum((d for _, d, _, _, _ in rows), ZERO), created_by=user_id)
    for led, d, c, n, j in rows:
        v.entries.append(AccEntry(organization_id=org_id, ledger_id=led.id, debit=d, credit=c, narration=n, job_number=j))
    db.add(v)
    db.flush()
    return v


def sync_document(db: Session, doc: Document, user_id=None, force=False) -> AccVoucher | None:
    """Mirror a document in the ledger: posted -> one voucher (replaced if it exists), anything else -> none."""
    org_id = doc.organization_id
    if not force and (doc.status == "POSTED" or db.scalar(select(AccVoucher.id).where(AccVoucher.organization_id == org_id, AccVoucher.source_type == "document", AccVoucher.source_id == doc.id))):
        assert_open(db, org_id, doc.doc_date)
    _drop(db, org_id, "document", doc.id)
    if doc.status != "POSTED" or doc.doc_type not in DOC_VOUCHER:
        return None
    coa.ensure_tree(db, org_id)
    sale = doc.doc_type in ("invoice", "debit_note", "credit_note")
    reverse = doc.doc_type in ("credit_note", "purchase_credit_note")
    cust = db.get(Customer, doc.customer_id) if doc.customer_id else None
    if cust is not None and cust.name != doc.party_name:      # the document names another party than the customer it points to: the party named on it is the one that is posted
        cust = None
    party = coa.party_ledger(db, org_id, doc.party_name, "debtor" if sale else "creditor", cust, user_id)
    side = "income" if sale else "expense"
    lob = "FREIGHT FORWARDING"
    body = defaultdict(lambda: ZERO)      # ledger id -> [ledger, amount]
    leds = {}
    for l in doc.lines:
        if l.tax_type == "Pure Agent":
            led = coa.sys_ledger(db, org_id, "pure_agent")
        else:
            led = coa.charge_ledger(db, org_id, l.charge_name, side, lob)
        leds[led.id] = led
        body[led.id] += q2(l.amount)
    tax = q2(doc.tax_total)
    intra = doc.supply_type == "intra"
    pref = "out_" if sale else "in_"
    taxes = []
    if tax:
        if intra:
            half = (tax / 2).quantize(Decimal("0.01"))
            taxes = [(coa.sys_ledger(db, org_id, pref + "cgst"), half), (coa.sys_ledger(db, org_id, pref + "sgst"), tax - half)]
        else:
            taxes = [(coa.sys_ledger(db, org_id, pref + "igst"), tax)]
    total = q2(doc.grand_total)
    # a sale (or purchase credit note) debits the party; a purchase (or credit note) credits it
    party_dr = (sale and not reverse) or (not sale and reverse)
    rows = [(party, total if party_dr else ZERO, ZERO if party_dr else total, doc.remarks, doc.job_number)]
    for lid, amt in body.items():
        rows.append((leds[lid], ZERO if party_dr else amt, amt if party_dr else ZERO, None, doc.job_number))
    for led, amt in taxes:
        rows.append((led, ZERO if party_dr else amt, amt if party_dr else ZERO, None, doc.job_number))
    return _voucher(db, org_id, DOC_VOUCHER[doc.doc_type], doc.doc_number or f"#{doc.id}", doc.doc_date, rows, source_type="document", source_id=doc.id, narration=f"{VOUCHER_LABEL[DOC_VOUCHER[doc.doc_type]]} {doc.doc_number or ''} {doc.party_name}".strip(),
                    party=doc.party_name, job=doc.job_number, user_id=user_id)


def sync_payment(db: Session, p: Payment, user_id=None, force=False) -> AccVoucher | None:
    org_id = p.organization_id
    if not force:
        assert_open(db, org_id, p.pay_date)
    _drop(db, org_id, "payment", p.id)
    if p.status != "POSTED":
        return None
    coa.ensure_tree(db, org_id)
    recv = p.direction == "RECEIPT"
    tds = sum((q2(a.tds_amount) for a in p.allocations), ZERO)
    ro = sum((q2(a.round_off) for a in p.allocations), ZERO)
    money = q2(p.amount)
    det = p.details or {}
    bank = coa.bank_ledger(db, org_id, p.method, det.get("account"))
    cust = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == p.party_name)) if p.party_name else None
    party = coa.party_ledger(db, org_id, p.party_name or "Unallocated", "debtor" if recv else "creditor", cust, user_id)
    job = None
    if recv:
        rows = [(bank, money, ZERO, p.reference, None), (party, ZERO, money + tds + ro, None, None)]
        if tds:
            rows.append((coa.sys_ledger(db, org_id, "tds_recv"), tds, ZERO, "TDS deducted by the payer", None))
        if ro:
            rows.append((coa.sys_ledger(db, org_id, "round_off"), ro, ZERO, "Written off to close the invoice", None))
    else:
        rows = [(party, money + tds + ro, ZERO, None, None), (bank, ZERO, money, p.reference, None)]
        if tds:
            rows.append((coa.sys_ledger(db, org_id, "tds_pay"), ZERO, tds, "TDS deducted from the vendor", None))
        if ro:
            rows.append((coa.sys_ledger(db, org_id, "round_off"), ZERO, ro, "Written off to close the bill", None))
    return _voucher(db, org_id, "RECEIPT" if recv else "PAYMENT", p.pay_number, p.pay_date, rows, source_type="payment", source_id=p.id, narration=f"{p.method} {p.reference or ''} {p.party_name}".strip(), party=p.party_name, job=job, user_id=user_id)


def rebuild(db: Session, org_id: int, user_id=None) -> dict:
    """Post every existing document and payment again (used once for companies that existed before the ledger, and on demand). Manual vouchers are left alone."""
    coa.ensure_tree(db, org_id)
    coa.sync_all(db, org_id, user_id)
    n = {"documents": 0, "payments": 0}
    for d in db.scalars(select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id).order_by(Document.doc_date, Document.id)):
        if sync_document(db, d, user_id, force=True):
            n["documents"] += 1
        elif d.status != "POSTED":
            pass
    for p in db.scalars(select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id).order_by(Payment.pay_date, Payment.id)):
        if sync_payment(db, p, user_id, force=True):
            n["payments"] += 1
    db.flush()
    return n


def ensure_posted(db: Session, org_id: int, user_id=None) -> None:
    """A company whose documents were posted before the ledger existed gets its vouchers once, on first use of a ledger report."""
    if db.scalar(select(func.count()).select_from(AccVoucher).where(AccVoucher.organization_id == org_id, AccVoucher.source_type.isnot(None))):
        return
    has = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == org_id, Document.status == "POSTED")) or db.scalar(select(func.count()).select_from(Payment).where(Payment.organization_id == org_id, Payment.status == "POSTED"))
    if has:
        rebuild(db, org_id, user_id)
        db.commit()
