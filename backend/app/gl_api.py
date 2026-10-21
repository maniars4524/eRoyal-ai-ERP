"""General ledger API: ledger report, trial balance / group summary, profit and loss, balance sheet, day book, manual journal and contra vouchers, rebuild."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import coa, gl
from .auth import permission_required
from .auth_api import record_audit
from .billing import fiscal_year
from .coa_api import _build
from .db import get_db
from .models import AccEntry, AccGroup, AccLedger, AccVoucher, HblCounter, User

router = APIRouter(prefix="/api/gl", tags=["general-ledger"])
ZERO = Decimal(0)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "The general ledger is tenant-scoped")
    return user.organization_id


def _s(v) -> str:
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


def _prep(db: Session, user: User) -> int:
    org_id = _tenant(user)
    coa.ensure_tree(db, org_id)
    gl.ensure_posted(db, org_id, user.id)
    return org_id


def _agg(db: Session, org_id: int, dfrom: date | None = None, dto: date | None = None, before: date | None = None) -> dict[int, tuple[Decimal, Decimal]]:
    """Debit / credit totals per ledger of posted vouchers inside the period, or strictly before `before`."""
    q = select(AccEntry.ledger_id, func.coalesce(func.sum(AccEntry.debit), 0), func.coalesce(func.sum(AccEntry.credit), 0)).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.organization_id == org_id, AccVoucher.status == "POSTED")
    if dfrom:
        q = q.where(AccVoucher.vdate >= dfrom)
    if dto:
        q = q.where(AccVoucher.vdate <= dto)
    if before:
        q = q.where(AccVoucher.vdate < before)
    return {lid: (Decimal(d), Decimal(c)) for lid, d, c in db.execute(q.group_by(AccEntry.ledger_id))}


def _opening(l: AccLedger) -> Decimal:
    v = Decimal(l.opening_balance or 0)
    return v if l.opening_side == "Dr" else -v


def _rows(roots: list[dict], opening: dict, period: dict, only_group: int | None = None) -> tuple[list[dict], dict]:
    """Roll ledger balances up the group tree. Row amounts: opening / closing are signed (debit positive). Empty groups are left out."""
    tot = {"opening": ZERO, "debit": ZERO, "credit": ZERO}

    def node(g, level, scope):
        scope = scope or only_group is None or g["id"] == only_group
        here = {"kind": "group", "level": level, "id": g["id"], "code": g["code"], "name": g["name"], "nature": g["nature"], "opening": ZERO, "debit": ZERO, "credit": ZERO, "closing": ZERO}
        rows: list[dict] = []
        for k in g["groups"]:
            ch, ch_rows = node(k, level + 1, scope)
            for f in ("opening", "debit", "credit"):
                here[f] += ch[f]
            rows += ch_rows
        for l in g["ledgers"]:
            o, (d, c) = opening.get(l["id"], ZERO), period.get(l["id"], (ZERO, ZERO))
            if not (o or d or c):
                continue
            if scope:
                rows.append({"kind": "ledger", "level": level + 1, "id": l["id"], "code": l["code"], "name": l["name"], "nature": g["nature"], "opening": o, "debit": d, "credit": c, "closing": o + d - c})
            for f, v in (("opening", o), ("debit", d), ("credit", c)):
                here[f] += v
        here["closing"] = here["opening"] + here["debit"] - here["credit"]
        if scope and (here["opening"] or here["debit"] or here["credit"]):
            rows.insert(0, here)
        return here, rows

    out: list[dict] = []
    for r in roots:
        here, rows = node(r, 0, False)
        out += rows
        for f in tot:
            tot[f] += here[f]
    return out, tot


def _fmt(rows: list[dict]) -> list[dict]:
    return [{**r, "opening": _s(r["opening"]), "debit": _s(r["debit"]), "credit": _s(r["credit"]), "closing": _s(r["closing"])} for r in rows]


def _opening_map(db: Session, org_id: int, before: date | None) -> dict[int, Decimal]:
    prior = _agg(db, org_id, before=before) if before else {}
    return {l.id: _opening(l) + prior.get(l.id, (ZERO, ZERO))[0] - prior.get(l.id, (ZERO, ZERO))[1] for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id))}


@router.get("/trial-balance")
def trial_balance(date_from: date | None = None, date_to: date | None = None, group_id: int | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Opening, period debit / credit and closing of every group and ledger; with group_id it is the Group Summary of that group."""
    org_id = _prep(db, user)
    roots, _, _ = _build(db, org_id)
    rows, _ = _rows(roots, _opening_map(db, org_id, date_from), _agg(db, org_id, date_from, date_to), group_id)
    led = [r for r in rows if r["kind"] == "ledger"]
    dr_close = sum((r["closing"] for r in led if r["closing"] > 0), ZERO)
    cr_close = -sum((r["closing"] for r in led if r["closing"] < 0), ZERO)
    totals = {"opening_dr": sum((r["opening"] for r in led if r["opening"] > 0), ZERO), "opening_cr": -sum((r["opening"] for r in led if r["opening"] < 0), ZERO), "debit": sum((r["debit"] for r in led), ZERO),
              "credit": sum((r["credit"] for r in led), ZERO), "closing_dr": dr_close, "closing_cr": cr_close}
    return {"date_from": date_from, "date_to": date_to, "rows": _fmt(rows), "totals": {k: _s(v) for k, v in totals.items()}, "balanced": dr_close == cr_close}


@router.get("/ledger")
def ledger_report(ledger_id: int, date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Account transactions of one ledger with a running balance."""
    org_id = _prep(db, user)
    led = db.get(AccLedger, ledger_id)
    if led is None or led.organization_id != org_id:
        raise HTTPException(404, "Ledger not found")
    prior = _agg(db, org_id, before=date_from).get(ledger_id, (ZERO, ZERO)) if date_from else (ZERO, ZERO)
    opening = _opening(led) + prior[0] - prior[1]
    q = select(AccEntry, AccVoucher).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id == ledger_id, AccVoucher.status == "POSTED").order_by(AccVoucher.vdate, AccVoucher.id, AccEntry.id)
    if date_from:
        q = q.where(AccVoucher.vdate >= date_from)
    if date_to:
        q = q.where(AccVoucher.vdate <= date_to)
    bal, rows, td, tc = opening, [], ZERO, ZERO
    for e, v in db.execute(q):
        bal += e.debit - e.credit
        td += e.debit
        tc += e.credit
        rows.append({"date": v.vdate, "voucher_id": v.id, "voucher_type": v.voucher_type, "voucher_label": gl.VOUCHER_LABEL.get(v.voucher_type, v.voucher_type), "number": v.number, "party": v.party_name, "job_number": e.job_number or v.job_number,
                     "narration": e.narration or v.narration, "debit": _s(e.debit), "credit": _s(e.credit), "balance": _s(bal), "source_type": v.source_type, "source_id": v.source_id})
    g = db.get(AccGroup, led.group_id)
    return {"ledger": {"id": led.id, "code": led.code, "name": led.name, "group": " > ".join(x.name for x in coa.group_path(db, g)), "nature": g.nature}, "opening": _s(opening), "entries": rows, "total_debit": _s(td), "total_credit": _s(tc), "closing": _s(bal)}


def _nature_total(rows: list[dict], nature: str, sign: int) -> Decimal:
    return sum((sign * (r["closing"] if r["kind"] == "group" else ZERO) for r in rows if r["level"] == 0 and r["nature"] == nature), ZERO)


@router.get("/profit-loss")
def profit_loss(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _prep(db, user)
    roots, _, _ = _build(db, org_id)
    period = _agg(db, org_id, date_from, date_to)
    rows, _ = _rows(roots, {}, period)    # movement of the period only
    income = sum((r["credit"] - r["debit"] for r in rows if r["level"] == 0 and r["nature"] == "INCOME"), ZERO)
    expense = sum((r["debit"] - r["credit"] for r in rows if r["level"] == 0 and r["nature"] == "EXPENSES"), ZERO)

    def section(nature, sign):
        out = []
        for r in rows:
            if r["nature"] != nature:
                continue
            amt = sign * (r["credit"] - r["debit"]) if nature == "INCOME" else sign * (r["debit"] - r["credit"])
            out.append({"kind": r["kind"], "level": r["level"], "code": r["code"], "name": r["name"], "amount": _s(amt)})
        return out

    def top(nature, name):
        return sum((r["credit"] - r["debit"] if nature == "INCOME" else r["debit"] - r["credit"] for r in rows if r["kind"] == "group" and r["level"] == 1 and r["name"] == name and r["nature"] == nature), ZERO)

    direct_inc, direct_exp = top("INCOME", "DIRECT INCOMES"), top("EXPENSES", "DIRECT EXPENSES")
    return {"date_from": date_from, "date_to": date_to, "income": section("INCOME", 1), "expenses": section("EXPENSES", 1), "total_income": _s(income), "total_expenses": _s(expense), "gross_profit": _s(direct_inc - direct_exp),
            "net_profit": _s(income - expense)}


@router.get("/balance-sheet")
def balance_sheet(as_on: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _prep(db, user)
    roots, _, _ = _build(db, org_id)
    rows, _ = _rows(roots, _opening_map(db, org_id, None), _agg(db, org_id, None, as_on))
    assets = sum((r["closing"] for r in rows if r["level"] == 0 and r["nature"] == "ASSETS"), ZERO)
    liab = -sum((r["closing"] for r in rows if r["level"] == 0 and r["nature"] == "LIABILITIES"), ZERO)
    profit = -sum((r["closing"] for r in rows if r["level"] == 0 and r["nature"] in ("INCOME", "EXPENSES")), ZERO)

    def sec(nature, sign):
        return [{"kind": r["kind"], "level": r["level"], "code": r["code"], "name": r["name"], "amount": _s(sign * r["closing"])} for r in rows if r["nature"] == nature]
    diff = assets - liab - profit
    return {"as_on": as_on, "assets": sec("ASSETS", 1), "liabilities": sec("LIABILITIES", -1), "total_assets": _s(assets), "total_liabilities": _s(liab), "profit_loss": _s(profit), "difference": _s(diff),
            "total_right": _s(liab + profit + diff), "balanced": diff == 0}


@router.get("/day-book")
def day_book(date_from: date | None = None, date_to: date | None = None, voucher_type: str | None = Query(default=None, max_length=24), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _prep(db, user)
    q = select(AccVoucher).where(AccVoucher.organization_id == org_id, AccVoucher.status == "POSTED").order_by(AccVoucher.vdate, AccVoucher.id)
    if date_from:
        q = q.where(AccVoucher.vdate >= date_from)
    if date_to:
        q = q.where(AccVoucher.vdate <= date_to)
    if voucher_type:
        q = q.where(AccVoucher.voucher_type == voucher_type)
    vs = list(db.scalars(q.limit(2000)))
    return {"vouchers": [{"id": v.id, "date": v.vdate, "type": v.voucher_type, "label": gl.VOUCHER_LABEL.get(v.voucher_type, v.voucher_type), "number": v.number, "party": v.party_name, "narration": v.narration, "total": _s(v.total), "source_type": v.source_type, "source_id": v.source_id,
                          "job_number": v.job_number} for v in vs], "total": _s(sum((v.total for v in vs), ZERO))}


def _voucher_out(db: Session, v: AccVoucher) -> dict:
    leds = {l.id: l for l in db.scalars(select(AccLedger).where(AccLedger.id.in_([e.ledger_id for e in v.entries])))}
    return {"id": v.id, "type": v.voucher_type, "label": gl.VOUCHER_LABEL.get(v.voucher_type, v.voucher_type), "number": v.number, "date": v.vdate, "narration": v.narration, "party": v.party_name, "job_number": v.job_number, "status": v.status,
            "source_type": v.source_type, "source_id": v.source_id, "total": _s(v.total), "entries": [{"ledger_id": e.ledger_id, "code": leds[e.ledger_id].code, "ledger": leds[e.ledger_id].name, "debit": _s(e.debit), "credit": _s(e.credit), "narration": e.narration} for e in v.entries]}


@router.get("/vouchers")
def list_vouchers(voucher_type: str = Query(pattern="^(JOURNAL|CONTRA)$"), date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _prep(db, user)
    q = select(AccVoucher).where(AccVoucher.organization_id == org_id, AccVoucher.voucher_type == voucher_type).order_by(AccVoucher.vdate.desc(), AccVoucher.id.desc())
    if date_from:
        q = q.where(AccVoucher.vdate >= date_from)
    if date_to:
        q = q.where(AccVoucher.vdate <= date_to)
    return [_voucher_out(db, v) for v in db.scalars(q.limit(500))]


@router.get("/vouchers/{vid}")
def get_voucher(vid: int, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    v = db.get(AccVoucher, vid)
    if v is None or v.organization_id != org_id:
        raise HTTPException(404, "Voucher not found")
    return _voucher_out(db, v)


class LineIn(BaseModel):
    ledger_id: int
    debit: Decimal = Field(default=ZERO, ge=0, max_digits=16, decimal_places=2)
    credit: Decimal = Field(default=ZERO, ge=0, max_digits=16, decimal_places=2)
    narration: str | None = Field(default=None, max_length=240)


class VoucherIn(BaseModel):
    voucher_type: str = Field(pattern="^(JOURNAL|CONTRA)$")
    vdate: date | None = None
    narration: str | None = Field(default=None, max_length=400)
    lines: list[LineIn] = Field(min_length=2, max_length=60)


@router.post("/vouchers", status_code=201)
def create_voucher(body: VoucherIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Journal voucher (any ledgers) or contra voucher (cash / bank to cash / bank). Debits must equal credits."""
    org_id = _prep(db, user)
    leds = {l.id: l for l in db.scalars(select(AccLedger).where(AccLedger.id.in_({x.ledger_id for x in body.lines}), AccLedger.organization_id == org_id))}
    for x in body.lines:
        l = leds.get(x.ledger_id)
        if l is None:
            raise HTTPException(404, "Ledger not found")
        if l.status != "ACTIVE":
            raise HTTPException(409, f"Ledger {l.name} is inactive")
        if (x.debit > 0) == (x.credit > 0):
            raise HTTPException(422, "Each line carries either a debit or a credit amount")
    dr, cr = sum((x.debit for x in body.lines), ZERO), sum((x.credit for x in body.lines), ZERO)
    if dr != cr:
        raise HTTPException(422, f"The voucher does not balance: debit {_s(dr)} and credit {_s(cr)}")
    if body.voucher_type == "CONTRA":
        for l in leds.values():
            g = db.get(AccGroup, l.group_id)
            if g.name not in ("BANK ACCOUNTS", "CASH-IN-HAND"):
                raise HTTPException(422, "A contra voucher moves money between cash and bank ledgers only")
    vdate = body.vdate or date.today()
    code = "JV" if body.voucher_type == "JOURNAL" else "CT"
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == org_id, HblCounter.hbl_type == code).with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=org_id, hbl_type=code, last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    number = f"{code}/{fiscal_year(vdate)}/{ctr.last_no:05d}"
    v = AccVoucher(organization_id=org_id, voucher_type=body.voucher_type, number=number, vdate=vdate, narration=body.narration, total=dr, created_by=user.id)
    for x in body.lines:
        v.entries.append(AccEntry(organization_id=org_id, ledger_id=x.ledger_id, debit=x.debit, credit=x.credit, narration=x.narration))
    db.add(v)
    db.flush()
    record_audit(db, "ACC_VOUCHER_CREATED", "ACCOUNTS", user, org_id, "AccVoucher", str(v.id), {"number": number, "type": v.voucher_type, "total": str(dr)})
    db.commit()
    return _voucher_out(db, v)


@router.post("/vouchers/{vid}/cancel")
def cancel_voucher(vid: int, user: User = Depends(permission_required("billing.cancel")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    v = db.get(AccVoucher, vid)
    if v is None or v.organization_id != org_id:
        raise HTTPException(404, "Voucher not found")
    if v.source_type:
        raise HTTPException(409, "This voucher mirrors an invoice or receipt. Cancel that document instead")
    if v.status == "CANCELLED":
        raise HTTPException(409, "Voucher is already cancelled")
    v.status = "CANCELLED"
    record_audit(db, "ACC_VOUCHER_CANCELLED", "ACCOUNTS", user, org_id, "AccVoucher", str(v.id), {"number": v.number})
    db.commit()
    return _voucher_out(db, v)


@router.post("/rebuild")
def rebuild(user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Post every posted invoice, note, receipt and payment into the ledger again. Safe to repeat; manual vouchers are untouched."""
    org_id = _tenant(user)
    n = gl.rebuild(db, org_id, user.id)
    record_audit(db, "GL_REBUILT", "ACCOUNTS", user, org_id, "AccVoucher", "all", n)
    db.commit()
    return n


# ---------------------------------------------------------------- bank reconciliation
def _bank_ledger(db: Session, org_id: int, ledger_id: int) -> AccLedger:
    led = db.get(AccLedger, ledger_id)
    if led is None or led.organization_id != org_id:
        raise HTTPException(404, "Ledger not found")
    if db.get(AccGroup, led.group_id).name != "BANK ACCOUNTS":
        raise HTTPException(422, "Bank reconciliation is done for bank ledgers only")
    return led


@router.get("/bank-recon")
def bank_recon(ledger_id: int, as_on: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Book balance against the bank statement: entries that have not cleared yet are the reconciling items."""
    org_id = _prep(db, user)
    led = _bank_ledger(db, org_id, ledger_id)
    q = select(AccEntry, AccVoucher).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id == ledger_id, AccVoucher.status == "POSTED").order_by(AccVoucher.vdate, AccVoucher.id, AccEntry.id)
    if as_on:
        q = q.where(AccVoucher.vdate <= as_on)
    book = _opening(led)
    cleared = _opening(led)
    items, dep_unc, pay_unc = [], ZERO, ZERO
    for e, v in db.execute(q):
        net = e.debit - e.credit
        book += net
        is_clear = e.cleared_date is not None and (as_on is None or e.cleared_date <= as_on)
        if is_clear:
            cleared += net
        else:
            dep_unc += e.debit
            pay_unc += e.credit
        items.append({"entry_id": e.id, "date": v.vdate, "voucher_id": v.id, "number": v.number, "type": gl.VOUCHER_LABEL.get(v.voucher_type, v.voucher_type), "party": v.party_name, "narration": e.narration or v.narration,
                      "debit": _s(e.debit), "credit": _s(e.credit), "cleared_date": e.cleared_date if is_clear else None})
    return {"ledger": {"id": led.id, "code": led.code, "name": led.name}, "as_on": as_on, "book_balance": _s(book), "cleared_balance": _s(cleared), "uncleared_deposits": _s(dep_unc), "uncleared_payments": _s(pay_unc),
            "balance_as_per_bank": _s(cleared), "items": items}


class ClearIn(BaseModel):
    ledger_id: int
    entry_ids: list[int] = Field(min_length=1, max_length=500)
    cleared_date: date | None = None   # None un-clears


@router.post("/bank-recon/clear")
def bank_clear(body: ClearIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    _bank_ledger(db, org_id, body.ledger_id)
    rows = list(db.scalars(select(AccEntry).where(AccEntry.id.in_(body.entry_ids), AccEntry.ledger_id == body.ledger_id, AccEntry.organization_id == org_id)))
    if len(rows) != len(set(body.entry_ids)):
        raise HTTPException(404, "Some entries do not belong to this bank ledger")
    for e in rows:
        e.cleared_date = body.cleared_date
    record_audit(db, "BANK_RECON_UPDATED", "ACCOUNTS", user, org_id, "AccLedger", str(body.ledger_id), {"entries": len(rows), "cleared_date": str(body.cleared_date)})
    db.commit()
    return {"updated": len(rows)}


# ---------------------------------------------------------------- GST
@router.get("/gst")
def gst_report(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Sales and purchase GST registers with the net GST payable. Credit notes reduce; the totals are cross-checked against the Output / Input GST ledgers."""
    from .models import Document

    org_id = _prep(db, user)
    q = select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED").order_by(Document.doc_date, Document.id)
    if date_from:
        q = q.where(Document.doc_date >= date_from)
    if date_to:
        q = q.where(Document.doc_date <= date_to)
    sales, purchases = [], []
    tot = {"sales": [ZERO] * 5, "purchases": [ZERO] * 5}
    for d in db.scalars(q):
        sale = d.doc_type in ("invoice", "debit_note", "credit_note")
        sign = -1 if d.doc_type in ("credit_note", "purchase_credit_note") else 1
        taxable = sum((l.amount for l in d.lines if l.tax_type == "Taxable"), ZERO)
        other = sum((l.amount for l in d.lines if l.tax_type != "Taxable"), ZERO)
        tax = d.tax_total or ZERO
        intra = d.supply_type == "intra"
        cg = (tax / 2).quantize(Decimal("0.01")) if intra else ZERO
        sg = tax - cg if intra else ZERO
        ig = ZERO if intra else tax
        row = {"date": d.doc_date, "number": d.doc_number, "type": d.doc_type, "party": d.party_name, "gstin": d.party_gstin, "place_of_supply": d.place_of_supply, "taxable": _s(sign * taxable), "non_taxable": _s(sign * other),
               "cgst": _s(sign * cg), "sgst": _s(sign * sg), "igst": _s(sign * ig), "total": _s(sign * d.grand_total)}
        (sales if sale else purchases).append(row)
        t = tot["sales" if sale else "purchases"]
        for i, v in enumerate((taxable, other, cg, sg, ig)):
            t[i] += sign * v
    names = ("taxable", "non_taxable", "cgst", "sgst", "igst")
    out_t, in_t = {k: _s(v) for k, v in zip(names, tot["sales"])}, {k: _s(v) for k, v in zip(names, tot["purchases"])}
    agg = _agg(db, org_id, date_from, date_to)
    keyed = {l.system_key: l.id for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.system_key.in_(("out_cgst", "out_sgst", "out_igst", "in_cgst", "in_sgst", "in_igst"))))}
    led_out = sum((agg.get(keyed[k], (ZERO, ZERO))[1] - agg.get(keyed[k], (ZERO, ZERO))[0] for k in ("out_cgst", "out_sgst", "out_igst")), ZERO)
    led_in = sum((agg.get(keyed[k], (ZERO, ZERO))[0] - agg.get(keyed[k], (ZERO, ZERO))[1] for k in ("in_cgst", "in_sgst", "in_igst")), ZERO)
    out_tax = tot["sales"][2] + tot["sales"][3] + tot["sales"][4]
    in_tax = tot["purchases"][2] + tot["purchases"][3] + tot["purchases"][4]
    return {"date_from": date_from, "date_to": date_to, "sales": sales, "purchases": purchases, "sales_total": out_t, "purchases_total": in_t, "output_tax": _s(out_tax), "input_tax": _s(in_tax), "net_payable": _s(out_tax - in_tax),
            "ledger_output": _s(led_out), "ledger_input": _s(led_in), "agrees_with_ledger": led_out == out_tax and led_in == in_tax}
