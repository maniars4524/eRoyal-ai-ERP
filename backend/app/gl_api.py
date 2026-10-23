"""General ledger API: ledger report, trial balance / group summary, profit and loss, balance sheet, day book, manual journal and contra vouchers, rebuild."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
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
    db.commit()
    gl.ensure_posted(db, org_id, user.id)
    return org_id


def _agg(db: Session, org_id: int, dfrom: date | None = None, dto: date | None = None, before: date | None = None, exclude_closing: bool = False) -> dict[int, tuple[Decimal, Decimal]]:
    """Debit / credit totals per ledger of posted vouchers inside the period, or strictly before `before`."""
    q = select(AccEntry.ledger_id, func.coalesce(func.sum(AccEntry.debit), 0), func.coalesce(func.sum(AccEntry.credit), 0)).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.organization_id == org_id, AccVoucher.status == "POSTED")
    if dfrom:
        q = q.where(AccVoucher.vdate >= dfrom)
    if dto:
        q = q.where(AccVoucher.vdate <= dto)
    if before:
        q = q.where(AccVoucher.vdate < before)
    if exclude_closing:
        q = q.where(AccVoucher.voucher_type != "CLOSING")
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
    period = _agg(db, org_id, date_from, date_to, exclude_closing=True)
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
    gl.assert_open(db, org_id, vdate)
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
        raise HTTPException(409, "This voucher is created by an invoice, receipt, payment or the year-end closing. Cancel that document (or reopen the year) instead")
    if v.status == "CANCELLED":
        raise HTTPException(409, "Voucher is already cancelled")
    gl.assert_open(db, org_id, v.vdate)
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


class StatementIn(BaseModel):
    ledger_id: int
    rows: list[dict] = Field(min_length=1, max_length=5000)
    apply: bool = False
    tolerance_days: int = Field(default=5, ge=0, le=60)


def _stmt_date(v) -> date | None:
    from datetime import datetime

    t = str(v or "").strip()[:10]
    for f in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(v or "").strip() if f in ("%d-%b-%Y", "%d %b %Y") else t, f).date()
        except ValueError:
            continue
    return None


def _stmt_amount(v) -> Decimal:
    t = str(v or "").replace(",", "").replace("₹", "").strip()
    try:
        return abs(Decimal(t)) if t else ZERO
    except Exception:
        raise ValueError("not a number: " + str(v))


@router.post("/bank-recon/import-statement")
def bank_statement_import(body: StatementIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Match the lines of a bank statement (date, debit = withdrawal, credit = deposit) with the uncleared entries of the bank ledger: same amount on the same side, voucher date within the tolerance
    (nearest date first, each entry used once). apply=false only shows the matches; apply=true clears the matched entries on the statement date."""
    org_id = _prep(db, user)
    led = _bank_ledger(db, org_id, body.ledger_id)
    q = select(AccEntry, AccVoucher).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id == led.id, AccVoucher.status == "POSTED", AccEntry.cleared_date.is_(None))
    pool = [(e, v) for e, v in db.execute(q)]
    used: set[int] = set()
    out, matched = [], []
    pick = lambda r, *names: next((r[k] for k in r if str(k).strip().lower().replace(" ", "_") in names), "")
    for i, r in enumerate(body.rows, start=1):
        d = _stmt_date(pick(r, "date", "txn_date", "transaction_date", "value_date"))
        try:
            dep = _stmt_amount(pick(r, "credit", "deposit", "deposits", "cr"))
            wd = _stmt_amount(pick(r, "debit", "withdrawal", "withdrawals", "dr"))
        except ValueError as ex:
            out.append({"row": i, "status": "ERROR", "message": str(ex)})
            continue
        ref = str(pick(r, "reference", "ref", "ref_no", "cheque_no", "utr", "description", "narration"))[:80]
        base = {"row": i, "date": d, "reference": ref, "deposit": _s(dep), "withdrawal": _s(wd)}
        if d is None or (dep > 0) == (wd > 0):
            out.append({**base, "status": "ERROR", "message": "Needs a valid date and either a debit or a credit amount"})
            continue
        best = None
        for e, v in pool:
            if e.id in used:
                continue
            if (dep > 0 and e.debit == dep and e.credit == 0) or (wd > 0 and e.credit == wd and e.debit == 0):
                gap = abs((v.vdate - d).days)
                if gap <= body.tolerance_days and (best is None or gap < best[0]):
                    best = (gap, e, v)
        if best is None:
            out.append({**base, "status": "UNMATCHED", "message": "No uncleared entry of the same amount near this date"})
            continue
        gap, e, v = best
        used.add(e.id)
        matched.append((e, d))
        out.append({**base, "status": "MATCHED", "entry_id": e.id, "voucher": v.number, "party": v.party_name, "message": f"Voucher {v.number} of {v.vdate:%d-%m-%Y}" + (f" ({gap} day(s) apart)" if gap else "")})
    result = {"ledger": {"id": led.id, "name": led.name}, "rows": out, "matched": len(matched), "unmatched": sum(1 for x in out if x["status"] == "UNMATCHED"), "errors": sum(1 for x in out if x["status"] == "ERROR"), "applied": False}
    if body.apply:
        for e, d in matched:
            e.cleared_date = d
        record_audit(db, "BANK_STATEMENT_IMPORTED", "ACCOUNTS", user, org_id, "AccLedger", str(led.id), {"matched": len(matched), "rows": len(body.rows)})
        db.commit()
        result["applied"] = True
    return result


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


# ---------------------------------------------------------------- TDS and RCM
@router.get("/tds")
def tds_report(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """TDS deducted by customers on our receipts (receivable, claimed against our tax) and TDS we deducted on vendor payments (payable to the department)."""
    from .models import Document, Payment

    org_id = _prep(db, user)
    q = select(Payment).options(selectinload(Payment.allocations)).where(Payment.organization_id == org_id, Payment.status == "POSTED").order_by(Payment.pay_date, Payment.id)
    if date_from:
        q = q.where(Payment.pay_date >= date_from)
    if date_to:
        q = q.where(Payment.pay_date <= date_to)
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.organization_id == org_id))}
    recv, pay = [], []
    for p in db.scalars(q):
        for a in p.allocations:
            if not a.tds_amount:
                continue
            d = docs.get(a.document_id)
            (recv if p.direction == "RECEIPT" else pay).append({"date": p.pay_date, "voucher": p.pay_number, "party": p.party_name, "document": d.doc_number if d else None, "document_total": _s(d.grand_total) if d else "0.00",
                                                                  "received": _s(a.amount), "tds": _s(a.tds_amount), "tds_percent": _s(a.tds_amount * 100 / (d.subtotal or 1)) if d and d.subtotal else "0.00"})
    tr, tp = sum((Decimal(r["tds"]) for r in recv), ZERO), sum((Decimal(r["tds"]) for r in pay), ZERO)
    agg = _agg(db, org_id, date_from, date_to)
    ids = {l.system_key: l.id for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.system_key.in_(("tds_recv", "tds_pay"))))}
    lr = agg.get(ids["tds_recv"], (ZERO, ZERO)); lp = agg.get(ids["tds_pay"], (ZERO, ZERO))
    return {"date_from": date_from, "date_to": date_to, "receivable": recv, "payable": pay, "total_receivable": _s(tr), "total_payable": _s(tp), "agrees_with_ledger": lr[0] - lr[1] == tr and lp[1] - lp[0] == tp}


@router.get("/rcm")
def rcm_report(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Reverse-charge (RCM) entries on posted documents: the recipient, not the supplier, pays the GST on these."""
    from .models import Document

    org_id = _prep(db, user)
    q = select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED").order_by(Document.doc_date, Document.id)
    if date_from:
        q = q.where(Document.doc_date >= date_from)
    if date_to:
        q = q.where(Document.doc_date <= date_to)
    rows, total_v, total_t = [], ZERO, ZERO
    for d in db.scalars(q):
        sign = -1 if d.doc_type in ("credit_note", "purchase_credit_note") else 1
        for l in d.lines:
            if l.tax_type != "RCM":
                continue
            tax = (l.amount * (l.tax_pct or ZERO) / 100).quantize(Decimal("0.01"))
            rows.append({"date": d.doc_date, "number": d.doc_number, "type": d.doc_type, "party": d.party_name, "gstin": d.party_gstin, "charge": l.charge_name, "hsn": l.hsn, "value": _s(sign * l.amount), "rate": _s(l.tax_pct), "tax": _s(sign * tax),
                         "intra": d.supply_type == "intra"})
            total_v += sign * l.amount
            total_t += sign * tax
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "total_value": _s(total_v), "total_tax": _s(total_t)}


# ---------------------------------------------------------------- HSN / SAC summary and cash flow
@router.get("/hsn-summary")
def hsn_summary(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Outward supplies by HSN / SAC: quantity, taxable value and GST (invoices and debit notes less credit notes)."""
    from .models import Document

    org_id = _prep(db, user)
    q = select(Document).options(selectinload(Document.lines)).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(("invoice", "debit_note", "credit_note"))).order_by(Document.doc_date)
    if date_from:
        q = q.where(Document.doc_date >= date_from)
    if date_to:
        q = q.where(Document.doc_date <= date_to)
    agg: dict = {}
    for d in db.scalars(q):
        sign = -1 if d.doc_type == "credit_note" else 1
        for l in d.lines:
            k = (l.hsn or "-", l.uom)
            a = agg.setdefault(k, {"hsn": k[0], "uom": k[1], "description": l.charge_name, "qty": ZERO, "value": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "documents": set()})
            a["qty"] += sign * l.qty
            a["value"] += sign * l.amount
            if d.supply_type == "intra":
                half = (l.tax_amount / 2).quantize(Decimal("0.01"))
                a["cgst"] += sign * half
                a["sgst"] += sign * (l.tax_amount - half)
            else:
                a["igst"] += sign * l.tax_amount
            a["documents"].add(d.id)
    rows = [{"hsn": a["hsn"], "uom": a["uom"], "description": a["description"], "documents": len(a["documents"]), "qty": str(a["qty"].normalize() if a["qty"] else 0), "value": _s(a["value"]), "cgst": _s(a["cgst"]), "sgst": _s(a["sgst"]),
             "igst": _s(a["igst"]), "tax": _s(a["cgst"] + a["sgst"] + a["igst"])} for a in sorted(agg.values(), key=lambda x: x["hsn"])]
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "total_value": _s(sum((Decimal(r["value"]) for r in rows), ZERO)), "total_tax": _s(sum((Decimal(r["tax"]) for r in rows), ZERO))}


@router.get("/cash-flow")
def cash_flow(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Cash and bank movement of the period by kind of voucher. Transfers between cash and bank (contra) are not a flow and are left out."""
    org_id = _prep(db, user)
    leds = [l for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id)) if db.get(AccGroup, l.group_id).name in ("BANK ACCOUNTS", "CASH-IN-HAND")]
    ids = [l.id for l in leds]
    if not ids:
        return {"opening": "0.00", "inflows": [], "outflows": [], "total_in": "0.00", "total_out": "0.00", "net": "0.00", "closing": "0.00", "date_from": date_from, "date_to": date_to}
    prior = _agg(db, org_id, before=date_from) if date_from else {}
    opening = sum((_opening(l) + prior.get(l.id, (ZERO, ZERO))[0] - prior.get(l.id, (ZERO, ZERO))[1] for l in leds), ZERO)
    q = select(AccVoucher.voucher_type, func.coalesce(func.sum(AccEntry.debit), 0), func.coalesce(func.sum(AccEntry.credit), 0)).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id.in_(ids), AccVoucher.status == "POSTED", AccVoucher.voucher_type != "CONTRA")
    if date_from:
        q = q.where(AccVoucher.vdate >= date_from)
    if date_to:
        q = q.where(AccVoucher.vdate <= date_to)
    ins, outs = [], []
    for vt, d, c in db.execute(q.group_by(AccVoucher.voucher_type)):
        if d:
            ins.append({"type": gl.VOUCHER_LABEL.get(vt, vt), "amount": _s(d)})
        if c:
            outs.append({"type": gl.VOUCHER_LABEL.get(vt, vt), "amount": _s(c)})
    ti, to = sum((Decimal(x["amount"]) for x in ins), ZERO), sum((Decimal(x["amount"]) for x in outs), ZERO)
    return {"date_from": date_from, "date_to": date_to, "opening": _s(opening), "inflows": ins, "outflows": outs, "total_in": _s(ti), "total_out": _s(to), "net": _s(ti - to), "closing": _s(opening + ti - to)}


# ---------------------------------------------------------------- staff salary and advances
class PayrollIn(BaseModel):
    kind: str = Field(pattern="^(SALARY|ADVANCE|RECOVERY)$")
    employee_id: int
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    pay_ledger_id: int
    vdate: date | None = None
    narration: str | None = Field(default=None, max_length=400)


@router.post("/payroll", status_code=201)
def payroll(body: PayrollIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Salary paid (Dr the employee's salary ledger, Cr cash / bank), an advance given to staff (Dr the employee's advance ledger, Cr cash / bank) or an advance repaid (Dr cash / bank, Cr the advance ledger)."""
    from .models import MasterRecord

    org_id = _prep(db, user)
    emp = db.get(MasterRecord, body.employee_id)
    if emp is None or emp.organization_id != org_id or emp.master_type != "employee":
        raise HTTPException(404, "Employee not found")
    pay = db.get(AccLedger, body.pay_ledger_id)
    if pay is None or pay.organization_id != org_id or db.get(AccGroup, pay.group_id).name not in ("BANK ACCOUNTS", "CASH-IN-HAND"):
        raise HTTPException(422, "Pay from a cash or bank ledger")
    sal = coa.sync_employee(db, emp, user.id)
    if body.kind == "SALARY":
        dr, cr, label = sal, pay, "Salary"
    else:
        adv = coa.staff_advance_ledger(db, emp, user.id)
        dr, cr, label = (adv, pay, "Advance") if body.kind == "ADVANCE" else (pay, adv, "Advance repaid")
    vdate = body.vdate or date.today()
    gl.assert_open(db, org_id, vdate)
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == org_id, HblCounter.hbl_type == "JV").with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=org_id, hbl_type="JV", last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    v = AccVoucher(organization_id=org_id, voucher_type="JOURNAL", number=f"JV/{fiscal_year(vdate)}/{ctr.last_no:05d}", vdate=vdate, narration=body.narration or f"{label} - {emp.name}", party_name=emp.name, total=body.amount, created_by=user.id)
    v.entries.append(AccEntry(organization_id=org_id, ledger_id=dr.id, debit=body.amount, credit=ZERO, narration=label))
    v.entries.append(AccEntry(organization_id=org_id, ledger_id=cr.id, debit=ZERO, credit=body.amount, narration=label))
    db.add(v)
    db.flush()
    record_audit(db, "PAYROLL_POSTED", "ACCOUNTS", user, org_id, "AccVoucher", str(v.id), {"kind": body.kind, "employee": emp.name, "amount": str(body.amount)})
    db.commit()
    return _voucher_out(db, v)


# ---------------------------------------------------------------- opening balance import
class OpeningRow(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    debit: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    credit: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)


class OpeningImport(BaseModel):
    rows: list[OpeningRow] = Field(min_length=1, max_length=5000)
    apply: bool = False


@router.post("/opening-import")
def opening_import(body: OpeningImport, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Set ledger opening balances from rows (code or name, debit, credit). With apply=false it only checks the rows. A row replaces that ledger's opening balance; ledgers that are not listed are left alone."""
    org_id = _tenant(user)
    coa.ensure_tree(db, org_id)
    db.commit()
    leds = list(db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id)))
    by_code = {l.code.upper(): l for l in leds}
    by_name: dict[str, list[AccLedger]] = {}
    for l in leds:
        by_name.setdefault(l.name.strip().lower(), []).append(l)
    out, ok, dr, cr = [], [], ZERO, ZERO
    seen: set[int] = set()
    for i, r in enumerate(body.rows, start=1):
        led, why = None, None
        if r.code and r.code.strip().upper() in by_code:
            led = by_code[r.code.strip().upper()]
        elif r.name and len(by_name.get(r.name.strip().lower(), [])) == 1:
            led = by_name[r.name.strip().lower()][0]
        elif r.name and len(by_name.get(r.name.strip().lower(), [])) > 1:
            why = "Several ledgers have this name. Use the ledger code"
        else:
            why = "No ledger with this code or name"
        if led is not None and led.id in seen:
            led, why = None, "The same ledger appears twice"
        if led is not None and r.debit > 0 and r.credit > 0:
            led, why = None, "A row carries either a debit or a credit"
        status = "OK" if led is not None else "ERROR"
        if led is not None:
            seen.add(led.id)
            ok.append((led, r))
            dr += r.debit
            cr += r.credit
        out.append({"row": i, "code": led.code if led else r.code, "name": led.name if led else r.name, "debit": _s(r.debit), "credit": _s(r.credit), "status": status, "message": why or ""})
    # the books as they would stand: listed ledgers replaced, the others as they are
    total_dr = ZERO
    total_cr = ZERO
    new = {l.id: (r.debit, r.credit) for l, r in ok}
    for l in leds:
        if l.id in new:
            d, c = new[l.id]
        else:
            v = Decimal(l.opening_balance or 0)
            d, c = (v, ZERO) if l.opening_side == "Dr" else (ZERO, v)
        total_dr += d
        total_cr += c
    result = {"rows": out, "valid": len(ok), "errors": len(out) - len(ok), "imported_debit": _s(dr), "imported_credit": _s(cr), "books_debit": _s(total_dr), "books_credit": _s(total_cr), "difference": _s(total_dr - total_cr), "applied": False}
    if body.apply:
        if gl.locked_until(db, org_id):
            raise HTTPException(409, "A financial year is closed, so opening balances can no longer be replaced. Reopen the years first")
        if result["errors"]:
            raise HTTPException(422, f"{result['errors']} row(s) have errors. Correct them or remove them and try again")
        for l, r in ok:
            l.opening_balance, l.opening_side = (r.debit, "Dr") if r.debit > 0 or r.credit == 0 else (r.credit, "Cr")
        record_audit(db, "OPENING_BALANCES_IMPORTED", "ACCOUNTS", user, org_id, "AccLedger", "bulk", {"ledgers": len(ok), "debit": str(dr), "credit": str(cr)})
        db.commit()
        result["applied"] = True
    return result


# ---------------------------------------------------------------- GST filing calendar and TCS
GST_DUE = {"GSTR1": 11, "GSTR3B": 20}      # monthly filers: day of the month after the supply period


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1)


@router.get("/gst-calendar")
def gst_calendar(fy: int | None = Query(default=None, ge=2000, le=2100), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """The twelve periods of a financial year (fy = the year it starts in) with the GSTR-1 and GSTR-3B due dates, what has been filed and the tax of the period."""
    from .models import GstFiling

    org_id = _prep(db, user)
    today = date.today()
    fy = fy or (today.year if today.month >= 4 else today.year - 1)
    filed = {(f.period, f.return_type): f for f in db.scalars(select(GstFiling).where(GstFiling.organization_id == org_id))}
    ids = {l.system_key: l.id for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.system_key.in_(("out_cgst", "out_sgst", "out_igst", "in_cgst", "in_sgst", "in_igst"))))}
    rows = []
    for k in range(12):
        y, m = (fy, 4 + k) if 4 + k <= 12 else (fy + 1, 4 + k - 12)
        period = f"{y}-{m:02d}"
        start, end = date(y, m, 1), _month_end(y, m)
        end = date.fromordinal(end.toordinal() - 1)
        agg = _agg(db, org_id, start, end)
        out_tax = sum((agg.get(ids[k2], (ZERO, ZERO))[1] - agg.get(ids[k2], (ZERO, ZERO))[0] for k2 in ("out_cgst", "out_sgst", "out_igst")), ZERO)
        in_tax = sum((agg.get(ids[k2], (ZERO, ZERO))[0] - agg.get(ids[k2], (ZERO, ZERO))[1] for k2 in ("in_cgst", "in_sgst", "in_igst")), ZERO)
        returns = []
        for rt, day in GST_DUE.items():
            ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
            due = date(ny, nm, day)
            f = filed.get((period, rt))
            if f:
                status = "FILED_LATE" if f.filed_on > due else "FILED"
            elif today > due:
                status = "OVERDUE"
            elif (due - today).days <= 7 and start <= today:
                status = "DUE_SOON"
            else:
                status = "UPCOMING"
            returns.append({"type": rt, "due": due, "status": status, "days_left": (due - today).days, "filed_on": f.filed_on if f else None, "ack_no": f.ack_no if f else None})
        rows.append({"period": period, "label": start.strftime("%b %Y"), "output_tax": _s(out_tax), "input_tax": _s(in_tax), "net_payable": _s(out_tax - in_tax), "returns": returns})
    return {"fy": fy, "fy_label": f"{fy}-{str(fy + 1)[2:]}", "periods": rows, "note": "Due dates are for monthly filers (GSTR-1 on the 11th, GSTR-3B on the 20th). Quarterly (QRMP) dates, extensions and notified waivers are not applied."}


class FilingIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    return_type: str = Field(pattern="^(GSTR1|GSTR3B)$")
    filed_on: date | None = None   # None removes the record
    ack_no: str | None = Field(default=None, max_length=40)


@router.post("/gst-calendar/mark")
def gst_mark(body: FilingIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    from .models import GstFiling

    org_id = _tenant(user)
    f = db.scalar(select(GstFiling).where(GstFiling.organization_id == org_id, GstFiling.period == body.period, GstFiling.return_type == body.return_type))
    if body.filed_on is None:
        if f:
            db.delete(f)
    else:
        if f is None:
            f = GstFiling(organization_id=org_id, period=body.period, return_type=body.return_type, filed_on=body.filed_on, filed_by=user.id)
            db.add(f)
        f.filed_on, f.ack_no = body.filed_on, (body.ack_no or "").strip() or None
    record_audit(db, "GST_FILING_MARKED", "ACCOUNTS", user, org_id, "GstFiling", f"{body.period}/{body.return_type}", {"filed_on": str(body.filed_on), "ack": body.ack_no})
    db.commit()
    return {"ok": True}


@router.get("/tcs")
def tcs_report(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Tax collected at source: movement of the TCS Receivable and TCS Payable ledgers (post them with a journal voucher)."""
    org_id = _prep(db, user)
    out = {"date_from": date_from, "date_to": date_to}
    for key, name in (("tcs_recv", "receivable"), ("tcs_pay", "payable")):
        led = coa.sys_ledger(db, org_id, key)
        q = select(AccEntry, AccVoucher).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id == led.id, AccVoucher.status == "POSTED").order_by(AccVoucher.vdate, AccVoucher.id)
        if date_from:
            q = q.where(AccVoucher.vdate >= date_from)
        if date_to:
            q = q.where(AccVoucher.vdate <= date_to)
        rows = [{"date": v.vdate, "voucher_id": v.id, "number": v.number, "party": v.party_name, "narration": e.narration or v.narration, "debit": _s(e.debit), "credit": _s(e.credit)} for e, v in db.execute(q)]
        d, c = sum((Decimal(r["debit"]) for r in rows), ZERO), sum((Decimal(r["credit"]) for r in rows), ZERO)
        out[name] = {"ledger": led.name, "ledger_id": led.id, "entries": rows, "debit": _s(d), "credit": _s(c), "net": _s(d - c if name == "receivable" else c - d)}
    return out


# ---------------------------------------------------------------- financial year closing
def _fy_bounds(fy: int) -> tuple[date, date]:
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def _fy_label(fy: int) -> str:
    return f"{fy}-{str(fy + 1)[2:]}"


def _fy_preview(db: Session, org_id: int, fy: int) -> dict:
    from .models import Document, FyClosing

    start, end = _fy_bounds(fy)
    agg = _agg(db, org_id, start, end, exclude_closing=True)
    leds = {l.id: l for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id))}
    groups = {g.id: g for g in db.scalars(select(AccGroup).where(AccGroup.organization_id == org_id))}
    income = expense = ZERO
    rows = []
    for lid, (d, c) in agg.items():
        l = leds.get(lid)
        if l is None or groups[l.group_id].nature not in ("INCOME", "EXPENSES") or d == c:
            continue
        net = d - c
        rows.append((l, net))
        if groups[l.group_id].nature == "INCOME":
            income += c - d
        else:
            expense += d - c
    drafts = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == org_id, Document.status.in_(("DRAFT", "CONFIRMED")), Document.doc_date >= start, Document.doc_date <= end)) or 0
    first = db.scalar(select(func.min(AccVoucher.vdate)).where(AccVoucher.organization_id == org_id, AccVoucher.status == "POSTED"))
    closed = {c.fy_start: c for c in db.scalars(select(FyClosing).where(FyClosing.organization_id == org_id))}
    earlier_open = []
    if first:
        y0 = first.year if first.month >= 4 else first.year - 1
        earlier_open = [_fy_label(y) for y in range(y0, fy) if y not in closed]
    today = date.today()
    blockers = []
    if fy in closed:
        blockers.append("This financial year is already closed")
    if end >= today:
        blockers.append("The financial year has not ended yet (it ends on " + end.strftime("%d-%m-%Y") + ")")
    if earlier_open:
        blockers.append("Close the earlier year(s) first: " + ", ".join(earlier_open))
    return {"fy": fy, "label": _fy_label(fy), "start": start, "end": end, "income": income, "expense": expense, "net_profit": income - expense, "ledgers": rows, "draft_documents": drafts, "blockers": blockers,
            "closed": closed.get(fy)}


@router.get("/fy")
def fy_list(user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    from .models import FyClosing

    org_id = _prep(db, user)
    today = date.today()
    cur = today.year if today.month >= 4 else today.year - 1
    first = db.scalar(select(func.min(AccVoucher.vdate)).where(AccVoucher.organization_id == org_id, AccVoucher.status == "POSTED"))
    y0 = min(cur, (first.year if first and first.month >= 4 else (first.year - 1 if first else cur)))
    closed = {c.fy_start: c for c in db.scalars(select(FyClosing).where(FyClosing.organization_id == org_id))}
    years = []
    for y in range(y0, cur + 1):
        p = _fy_preview(db, org_id, y)
        c = closed.get(y)
        years.append({"fy": y, "label": p["label"], "start": p["start"], "end": p["end"], "status": "CLOSED" if c else ("CURRENT" if y == cur else "OPEN"), "net_profit": _s(c.net_profit if c else p["net_profit"]), "income": _s(p["income"]), "expense": _s(p["expense"]),
                      "draft_documents": p["draft_documents"], "blockers": p["blockers"], "closed_at": c.closed_at if c else None, "voucher_id": c.voucher_id if c else None})
    return {"years": years, "locked_until": gl.locked_until(db, org_id)}


class FyIn(BaseModel):
    fy: int = Field(ge=2000, le=2100)
    ignore_drafts: bool = False


@router.post("/fy/close")
def fy_close(body: FyIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Close a financial year: its income and expense ledgers are cleared into Profit & Loss Account (Reserves) by a closing voucher dated the last day of the year, and the year is locked."""
    from .models import FyClosing

    org_id = _prep(db, user)
    p = _fy_preview(db, org_id, body.fy)
    if p["blockers"]:
        raise HTTPException(409, "; ".join(p["blockers"]))
    if p["draft_documents"] and not body.ignore_drafts:
        raise HTTPException(409, f"{p['draft_documents']} draft or proforma document(s) are dated in this year. Post or delete them first, or close anyway")
    reserve = coa.sys_ledger(db, org_id, "reserve")
    rows = []
    for l, net in p["ledgers"]:
        rows.append((l, abs(net) if net < 0 else ZERO, net if net > 0 else ZERO, "Closing entry", None))
    profit = p["net_profit"]
    if profit:
        rows.append((reserve, ZERO if profit > 0 else -profit, profit if profit > 0 else ZERO, f"Net {'profit' if profit > 0 else 'loss'} of {p['label']}", None))
    v = None
    if rows:
        v = gl._voucher(db, org_id, "CLOSING", f"CL/{p['label']}", p["end"], rows, source_type="fy_close", source_id=body.fy, narration=f"Closing of financial year {p['label']}", user_id=user.id)
    db.add(FyClosing(organization_id=org_id, fy_start=body.fy, start_date=p["start"], end_date=p["end"], net_profit=profit, voucher_id=v.id if v else None, closed_by=user.id))
    record_audit(db, "FY_CLOSED", "ACCOUNTS", user, org_id, "FyClosing", p["label"], {"net_profit": str(profit), "ledgers": len(p["ledgers"])})
    db.commit()
    return {"label": p["label"], "net_profit": _s(profit), "ledgers_closed": len(p["ledgers"]), "voucher_id": v.id if v else None, "locked_until": p["end"]}


@router.post("/fy/reopen")
def fy_reopen(body: FyIn, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Reopen the latest closed year: its closing voucher is removed and the lock lifted."""
    from .models import FyClosing

    org_id = _tenant(user)
    c = db.scalar(select(FyClosing).where(FyClosing.organization_id == org_id, FyClosing.fy_start == body.fy))
    if c is None:
        raise HTTPException(404, "This financial year is not closed")
    later = db.scalar(select(func.count()).select_from(FyClosing).where(FyClosing.organization_id == org_id, FyClosing.fy_start > body.fy))
    if later:
        raise HTTPException(409, "A later financial year is closed. Reopen the latest closed year first")
    if c.voucher_id:
        gl._drop(db, org_id, "fy_close", body.fy)
    db.delete(c)
    record_audit(db, "FY_REOPENED", "ACCOUNTS", user, org_id, "FyClosing", _fy_label(body.fy), {})
    db.commit()
    return {"label": _fy_label(body.fy), "locked_until": gl.locked_until(db, org_id)}


# ---------------------------------------------------------------- GSTR-2B and ITC reconciliation
class Gstr2bRowIn(BaseModel):
    gstin: str = Field(min_length=15, max_length=15)
    supplier: str | None = Field(default=None, max_length=200)
    doc_kind: str = Field(default="INV", pattern="^(INV|CN|DN)$")
    inv_no: str = Field(min_length=1, max_length=60)
    inv_date: date | None = None
    taxable: Decimal = Field(default=ZERO, max_digits=16, decimal_places=2)
    cgst: Decimal = Field(default=ZERO, max_digits=16, decimal_places=2)
    sgst: Decimal = Field(default=ZERO, max_digits=16, decimal_places=2)
    igst: Decimal = Field(default=ZERO, max_digits=16, decimal_places=2)
    itc_available: bool = True


class Gstr2bImport(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    rows: list[Gstr2bRowIn] = Field(min_length=1, max_length=20000)


@router.post("/gstr2b/import", status_code=201)
def gstr2b_import(body: Gstr2bImport, user: User = Depends(permission_required("billing.post")), db: Session = Depends(get_db)):
    """Store the rows of a GSTR-2B statement for a period (what you download from the GST portal). Importing the same period again replaces the earlier rows. Credit notes must carry negative amounts."""
    from .models import Gstr2bRow

    org_id = _tenant(user)
    db.execute(delete(Gstr2bRow).where(Gstr2bRow.organization_id == org_id, Gstr2bRow.period == body.period))
    for r in body.rows:
        sign = -1 if r.doc_kind == "CN" else 1
        db.add(Gstr2bRow(organization_id=org_id, period=body.period, gstin=r.gstin.upper(), supplier=r.supplier, doc_kind=r.doc_kind, inv_no=r.inv_no.strip(), inv_date=r.inv_date, taxable=sign * abs(r.taxable), cgst=sign * abs(r.cgst),
                         sgst=sign * abs(r.sgst), igst=sign * abs(r.igst), itc_available=r.itc_available, imported_by=user.id))
    record_audit(db, "GSTR2B_IMPORTED", "ACCOUNTS", user, org_id, "Gstr2bRow", body.period, {"rows": len(body.rows)})
    db.commit()
    return {"period": body.period, "rows": len(body.rows)}


def _norm_inv(v: str | None) -> str:
    s = re.sub(r"[^A-Z0-9]", "", (v or "").upper())
    return s.lstrip("0") or s


@router.get("/itc-reconciliation")
def itc_reconciliation(period: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Purchase invoices and credit notes in the books against the GSTR-2B of the period. Matched by supplier GSTIN and invoice number (ignoring spaces, dashes and leading zeros), values within Rs 1."""
    from .models import Document, Gstr2bRow

    org_id = _prep(db, user)
    y, m = int(period[:4]), int(period[5:])
    start, end = date(y, m, 1), date.fromordinal(date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1)
    books = []
    for d in db.scalars(select(Document).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(("purchase_invoice", "purchase_credit_note")), Document.tax_total != 0)):
        h = d.header or {}
        try:
            when = date.fromisoformat(h.get("vendor_inv_date")) if h.get("vendor_inv_date") else d.doc_date
        except ValueError:
            when = d.doc_date
        if not (start <= when <= end):
            continue
        sign = -1 if d.doc_type == "purchase_credit_note" else 1
        taxable = sum((l.amount for l in d.lines if l.tax_type == "Taxable"), ZERO) if d.lines else d.subtotal
        books.append({"id": d.id, "doc": d.doc_number, "party": d.party_name, "gstin": (d.party_gstin or "").upper(), "inv_no": h.get("vendor_inv_no") or "", "date": when, "taxable": sign * taxable, "tax": sign * d.tax_total})
    sheet = [{"id": r.id, "gstin": r.gstin.upper(), "supplier": r.supplier, "inv_no": r.inv_no, "date": r.inv_date, "taxable": r.taxable, "tax": r.cgst + r.sgst + r.igst, "itc": r.itc_available, "kind": r.doc_kind}
             for r in db.scalars(select(Gstr2bRow).where(Gstr2bRow.organization_id == org_id, Gstr2bRow.period == period))]
    by_key: dict = {}
    for r in sheet:
        by_key.setdefault((r["gstin"], _norm_inv(r["inv_no"])), []).append(r)
    used: set[int] = set()
    rows = []
    close = lambda a, b: abs(a - b) <= Decimal(1)

    def line(status, b, r, note=""):
        return {"status": status, "gstin": (b or r)["gstin"], "party": (b["party"] if b else r["supplier"]) or "", "book_doc": b["doc"] if b else None, "book_inv_no": b["inv_no"] if b else None, "book_taxable": _s(b["taxable"]) if b else None,
                "book_tax": _s(b["tax"]) if b else None, "gstr_inv_no": r["inv_no"] if r else None, "gstr_taxable": _s(r["taxable"]) if r else None, "gstr_tax": _s(r["tax"]) if r else None, "itc_available": r["itc"] if r else None, "note": note,
                "difference": _s((b["tax"] if b else ZERO) - (r["tax"] if r else ZERO))}

    leftovers = []
    for b in books:
        cands = [r for r in by_key.get((b["gstin"], _norm_inv(b["inv_no"])), []) if r["id"] not in used] if b["inv_no"] else []
        if cands:
            r = cands[0]
            used.add(r["id"])
            ok = close(b["taxable"], r["taxable"]) and close(b["tax"], r["tax"])
            rows.append(line("MATCHED" if ok else "MISMATCH", b, r, "" if ok else "Values differ from the GSTR-2B"))
        else:
            leftovers.append(b)
    for b in leftovers:       # same supplier and the same amounts but a different invoice number
        guess = next((r for r in sheet if r["id"] not in used and r["gstin"] == b["gstin"] and close(b["taxable"], r["taxable"]) and close(b["tax"], r["tax"])), None)
        if guess:
            used.add(guess["id"])
            rows.append(line("PROBABLE", b, guess, "Same supplier and amounts, invoice number differs. Correct the vendor invoice number in the books" if b["inv_no"] else "Same supplier and amounts. The vendor invoice number is missing in the books"))
        else:
            rows.append(line("NOT_IN_2B", b, None, "Not reported by the supplier. ITC cannot be claimed until it appears"))
    for r in sheet:
        if r["id"] not in used:
            rows.append(line("NOT_IN_BOOKS", None, r, "Reported by the supplier but not booked. Book the purchase invoice to claim the ITC"))
    order = {"MISMATCH": 0, "NOT_IN_2B": 1, "NOT_IN_BOOKS": 2, "PROBABLE": 3, "MATCHED": 4}
    rows.sort(key=lambda x: (order[x["status"]], x["party"]))
    book_itc = sum((b["tax"] for b in books), ZERO)
    gstr_itc = sum((r["tax"] for r in sheet if r["itc"]), ZERO)
    matched = sum((Decimal(x["gstr_tax"]) for x in rows if x["status"] in ("MATCHED", "PROBABLE") and x["itc_available"]), ZERO)
    counts = {k: sum(1 for x in rows if x["status"] == k) for k in order}
    return {"period": period, "imported_rows": len(sheet), "books_rows": len(books), "itc_per_books": _s(book_itc), "itc_per_gstr2b": _s(gstr_itc), "itc_matched": _s(matched),
            "itc_at_risk": _s(sum((Decimal(x["book_tax"]) for x in rows if x["status"] == "NOT_IN_2B"), ZERO)), "itc_unbooked": _s(sum((Decimal(x["gstr_tax"]) for x in rows if x["status"] == "NOT_IN_BOOKS" and x["itc_available"]), ZERO)),
            "counts": counts, "rows": rows}
