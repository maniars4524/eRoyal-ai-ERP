"""MIS reports built from the posted documents, jobs, bookings and containers: executive summary, revenue, customer profitability, gross profit by job, shipments, carrier-wise TEUs and customer portfolio.
Revenue is the value of posted sales invoices and debit notes less credit notes (before tax); cost is posted purchase invoices less purchase credit notes (before tax)."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .auth import permission_required
from .billing import outstanding
from .db import get_db
from .models import AccEntry, AccGroup, AccLedger, AccVoucher, Booking, Customer, Document, JobDetail, Shipment, User

router = APIRouter(prefix="/api/mis", tags=["mis"])
ZERO = Decimal(0)
SALES = ("invoice", "debit_note", "credit_note")
COST = ("purchase_invoice", "purchase_credit_note")
NEG = ("credit_note", "purchase_credit_note")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "MIS reports are tenant-scoped")
    return user.organization_id


def _s(v) -> str:
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


def _pct(a: Decimal, b: Decimal) -> str:
    return _s(a * 100 / b) if b else "0.00"


def _docs(db: Session, org_id: int, date_from: date | None, date_to: date | None, types=SALES + COST) -> list[Document]:
    q = select(Document).where(Document.organization_id == org_id, Document.status == "POSTED", Document.doc_type.in_(types))
    if date_from:
        q = q.where(Document.doc_date >= date_from)
    if date_to:
        q = q.where(Document.doc_date <= date_to)
    return list(db.scalars(q.order_by(Document.doc_date, Document.id)))


def _val(d: Document) -> Decimal:
    return -d.subtotal if d.doc_type in NEG else d.subtotal


def _job_index(db: Session, org_id: int) -> dict[str, Shipment]:
    return {s.job_number: s for s in db.scalars(select(Shipment).where(Shipment.organization_id == org_id))}


@router.get("/executive")
def executive(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    docs = _docs(db, org_id, date_from, date_to)
    rev = sum((_val(d) for d in docs if d.doc_type in SALES), ZERO)
    cost = sum((_val(d) for d in docs if d.doc_type in COST), ZERO)
    open_docs = list(db.scalars(select(Document).where(Document.organization_id == org_id, Document.status == "POSTED")))
    today = date.today()
    ar = sum((outstanding(d) for d in open_docs if d.doc_type in ("invoice", "debit_note")), ZERO)
    ap = sum((outstanding(d) for d in open_docs if d.doc_type == "purchase_invoice"), ZERO)
    overdue = sum((outstanding(d) for d in open_docs if d.doc_type in ("invoice", "debit_note") and d.due_date and d.due_date < today and outstanding(d) > 0), ZERO)
    jq = select(func.count()).select_from(Shipment).where(Shipment.organization_id == org_id)
    jobs_total = db.scalar(jq) or 0
    jobs_open = db.scalar(jq.where(Shipment.closure_status == "OPEN")) or 0
    jobs_period = db.scalar(jq.where(*( [Shipment.created_at >= date_from] if date_from else []), *( [Shipment.created_at < date.fromordinal(date_to.toordinal() + 1)] if date_to else []))) or 0
    ids = [l.id for l in db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id)) if db.get(AccGroup, l.group_id).name in ("BANK ACCOUNTS", "CASH-IN-HAND")]
    cashbank = ZERO
    if ids:
        led = {l.id: l for l in db.scalars(select(AccLedger).where(AccLedger.id.in_(ids)))}
        for lid, dr, cr in db.execute(select(AccEntry.ledger_id, func.coalesce(func.sum(AccEntry.debit), 0), func.coalesce(func.sum(AccEntry.credit), 0)).join(AccVoucher, AccVoucher.id == AccEntry.voucher_id).where(AccEntry.ledger_id.in_(ids), AccVoucher.status == "POSTED").group_by(AccEntry.ledger_id)):
            cashbank += Decimal(dr) - Decimal(cr)
        cashbank += sum((l.opening_balance if l.opening_side == "Dr" else -l.opening_balance for l in led.values()), ZERO)
    # twelve month trend ending at the end of the period (or today)
    end = date_to or today
    months = []
    y, m = end.year, end.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    trend = {f"{y}-{m:02d}": [ZERO, ZERO] for y, m in months}
    for d in _docs(db, org_id, date(months[0][0], months[0][1], 1), end):
        k = f"{d.doc_date.year}-{d.doc_date.month:02d}"
        if k in trend:
            trend[k][0 if d.doc_type in SALES else 1] += _val(d)
    return {"date_from": date_from, "date_to": date_to, "revenue": _s(rev), "cost": _s(cost), "gross_profit": _s(rev - cost), "margin_percent": _pct(rev - cost, rev), "receivable": _s(ar), "overdue_receivable": _s(overdue), "payable": _s(ap),
            "cash_and_bank": _s(cashbank), "jobs_total": jobs_total, "jobs_open": jobs_open, "jobs_in_period": jobs_period, "trend": [{"month": k, "revenue": _s(v[0]), "cost": _s(v[1]), "profit": _s(v[0] - v[1])} for k, v in trend.items()]}


@router.get("/revenue")
def revenue(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    jobs = _job_index(db, org_id)
    by_month, by_cust, by_mode = defaultdict(lambda: ZERO), defaultdict(lambda: [ZERO, 0]), defaultdict(lambda: ZERO)
    total = ZERO
    for d in _docs(db, org_id, date_from, date_to, SALES):
        v = _val(d)
        total += v
        by_month[f"{d.doc_date.year}-{d.doc_date.month:02d}"] += v
        by_cust[d.party_name][0] += v
        by_cust[d.party_name][1] += 1
        j = jobs.get(d.job_number or "")
        by_mode[f"{j.mode} {j.direction}" if j else "Not linked to a job"] += v
    return {"date_from": date_from, "date_to": date_to, "total": _s(total), "months": [{"month": k, "revenue": _s(v)} for k, v in sorted(by_month.items())],
            "customers": [{"customer": k, "revenue": _s(v[0]), "documents": v[1], "share": _pct(v[0], total)} for k, v in sorted(by_cust.items(), key=lambda x: -x[1][0])],
            "modes": [{"mode": k, "revenue": _s(v), "share": _pct(v, total)} for k, v in sorted(by_mode.items(), key=lambda x: -x[1])]}


def _job_pl(db: Session, org_id: int, date_from, date_to):
    jobs = _job_index(db, org_id)
    rows: dict = {}
    for d in _docs(db, org_id, date_from, date_to):
        key = d.job_number or ""
        r = rows.setdefault(key, {"job": key, "revenue": ZERO, "cost": ZERO, "customer": None})
        if d.doc_type in SALES:
            r["revenue"] += _val(d)
            r["customer"] = r["customer"] or d.party_name
        else:
            r["cost"] += _val(d)
    return rows, jobs


@router.get("/gp-analysis")
def gp_analysis(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Revenue, cost and gross profit of every job from its posted documents, and the same by mode."""
    org_id = _tenant(user)
    rows, jobs = _job_pl(db, org_id, date_from, date_to)
    out, modes = [], defaultdict(lambda: [ZERO, ZERO, 0])
    for key, r in sorted(rows.items(), key=lambda x: -(x[1]["revenue"] - x[1]["cost"])):
        j = jobs.get(key)
        gp = r["revenue"] - r["cost"]
        label = f"{j.mode} {j.direction}" if j else "Not linked to a job"
        modes[label][0] += r["revenue"]
        modes[label][1] += r["cost"]
        modes[label][2] += 1 if key else 0
        out.append({"job": key or "(no job)", "customer": r["customer"] or "", "mode": label, "pol": j.pol if j else None, "pod": j.pod if j else None, "revenue": _s(r["revenue"]), "cost": _s(r["cost"]), "gross_profit": _s(gp), "margin_percent": _pct(gp, r["revenue"])})
    tr, tc = sum((r["revenue"] for r in rows.values()), ZERO), sum((r["cost"] for r in rows.values()), ZERO)
    return {"date_from": date_from, "date_to": date_to, "jobs": out, "modes": [{"mode": k, "jobs": v[2], "revenue": _s(v[0]), "cost": _s(v[1]), "gross_profit": _s(v[0] - v[1]), "margin_percent": _pct(v[0] - v[1], v[0])} for k, v in sorted(modes.items())],
            "total_revenue": _s(tr), "total_cost": _s(tc), "total_gross_profit": _s(tr - tc), "margin_percent": _pct(tr - tc, tr)}


@router.get("/customer-profitability")
def customer_profitability(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """Per customer: revenue billed, the cost of the jobs billed to them, gross profit and margin."""
    org_id = _tenant(user)
    rows, jobs = _job_pl(db, org_id, date_from, date_to)
    names = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.organization_id == org_id))}
    per: dict = defaultdict(lambda: {"revenue": ZERO, "cost": ZERO, "jobs": set()})
    for key, r in rows.items():
        j = jobs.get(key)
        cust = names.get(j.customer_id) if j and j.customer_id else None
        cust = cust or r["customer"] or "(no customer)"
        per[cust]["revenue"] += r["revenue"]
        per[cust]["cost"] += r["cost"]
        if key:
            per[cust]["jobs"].add(key)
    out = [{"customer": k, "jobs": len(v["jobs"]), "revenue": _s(v["revenue"]), "cost": _s(v["cost"]), "gross_profit": _s(v["revenue"] - v["cost"]), "margin_percent": _pct(v["revenue"] - v["cost"], v["revenue"])} for k, v in per.items()]
    out.sort(key=lambda x: -Decimal(x["gross_profit"]))
    return {"date_from": date_from, "date_to": date_to, "customers": out}


@router.get("/shipments")
def shipments(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    q = select(Shipment).where(Shipment.organization_id == org_id)
    if date_from:
        q = q.where(Shipment.created_at >= date_from)
    if date_to:
        q = q.where(Shipment.created_at < date.fromordinal(date_to.toordinal() + 1))
    by_mode, by_stage, by_month, lanes = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    n = 0
    for s in db.scalars(q):
        n += 1
        by_mode[f"{s.mode} {s.direction}"] += 1
        by_stage[s.job_stage or "Draft"] += 1
        by_month[s.created_at.strftime("%Y-%m")] += 1
        lanes[f"{s.pol or '?'} → {s.pod or '?'}"] += 1
    srt = lambda d, k=lambda x: x[0]: [{"name": a, "jobs": b} for a, b in sorted(d.items(), key=k)]
    return {"date_from": date_from, "date_to": date_to, "total": n, "modes": srt(by_mode, lambda x: -x[1]), "stages": srt(by_stage, lambda x: -x[1]), "months": srt(by_month), "lanes": srt(lanes, lambda x: -x[1])[:15]}


def _teu(kind: str) -> int:
    m = re.match(r"\s*(\d{2})", kind or "")
    return 2 if m and m.group(1) in ("40", "45") else 1


@router.get("/carrier-teu")
def carrier_teu(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Containers and TEUs by carrier: a 20' container is 1 TEU, a 40' or 45' container is 2 TEU. Containers come from the job's Containers tab, else from the booking."""
    org_id = _tenant(user)
    q = select(Shipment).where(Shipment.organization_id == org_id, Shipment.mode == "Sea")
    if date_from:
        q = q.where(Shipment.created_at >= date_from)
    if date_to:
        q = q.where(Shipment.created_at < date.fromordinal(date_to.toordinal() + 1))
    bookings = {b.id: b for b in db.scalars(select(Booking).where(Booking.organization_id == org_id))}
    details = {d.shipment_id: d for d in db.scalars(select(JobDetail).where(JobDetail.organization_id == org_id))}
    per: dict = defaultdict(lambda: {"jobs": 0, "c20": 0, "c40": 0, "teu": 0})
    for s in db.scalars(q):
        b = bookings.get(s.booking_id)
        carrier = (b.carrier if b and b.carrier else "(carrier not recorded)")
        conts = list((details.get(s.id).containers if details.get(s.id) else None) or [])
        kinds = [str(c.get("type") or "") for c in conts] or ([str((b.data or {}).get("container_type") or "")] if b and (b.data or {}).get("container_no") else [])
        r = per[carrier]
        r["jobs"] += 1
        for k in kinds:
            t = _teu(k)
            r["c40" if t == 2 else "c20"] += 1
            r["teu"] += t
    rows = [{"carrier": k, **v} for k, v in sorted(per.items(), key=lambda x: -x[1]["teu"])]
    return {"date_from": date_from, "date_to": date_to, "carriers": rows, "total_teu": sum(r["teu"] for r in rows), "total_containers": sum(r["c20"] + r["c40"] for r in rows)}


@router.get("/portfolio")
def portfolio(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    """The customer base: parties by role and status, new parties in the period and the top customers by revenue."""
    org_id = _tenant(user)
    custs = list(db.scalars(select(Customer).where(Customer.organization_id == org_id)))
    roles: dict = defaultdict(int)
    for c in custs:
        for r in (c.roles or []):
            roles[str(r)] += 1
    new = [c for c in custs if (not date_from or c.created_at.date() >= date_from) and (not date_to or c.created_at.date() <= date_to)] if (date_from or date_to) else []
    rev = defaultdict(lambda: ZERO)
    for d in _docs(db, org_id, date_from, date_to, SALES):
        rev[d.party_name] += _val(d)
    total = sum(rev.values(), ZERO)
    top = [{"customer": k, "revenue": _s(v), "share": _pct(v, total)} for k, v in sorted(rev.items(), key=lambda x: -x[1])[:15]]
    return {"date_from": date_from, "date_to": date_to, "parties": len(custs), "active": sum(1 for c in custs if c.status == "ACTIVE"), "inactive": sum(1 for c in custs if c.status != "ACTIVE"), "roles": [{"role": k, "parties": v} for k, v in sorted(roles.items(), key=lambda x: -x[1])],
            "new_in_period": len(new), "customers_billed": len(rev), "top_customers": top, "top5_share": _pct(sum((Decimal(t["revenue"]) for t in top[:5]), ZERO), total)}


def _by_job_field(db: Session, org_id: int, field: str, date_from: date | None, date_to: date | None) -> dict:
    """Revenue, cost and gross profit grouped by a field of the job (branch / salesperson). Documents are tied to the job they were raised for; the rest is shown as Unassigned."""
    jobs = _job_index(db, org_id)
    rows: dict[str, dict] = {}

    def row(name: str) -> dict:
        return rows.setdefault(name, {"name": name, "jobs": set(), "revenue": ZERO, "cost": ZERO})

    for j in jobs.values():
        if date_from and j.created_at.date() < date_from:
            continue
        if date_to and j.created_at.date() > date_to:
            continue
        row(getattr(j, field) or "Unassigned")["jobs"].add(j.job_number)
    for d in _docs(db, org_id, date_from, date_to):
        j = jobs.get(d.job_number or "")
        r = row((getattr(j, field) if j else None) or "Unassigned")
        if d.doc_type in SALES:
            r["revenue"] += _val(d)
        else:
            r["cost"] += _val(d)
    out = []
    for r in sorted(rows.values(), key=lambda x: -x["revenue"]):
        gp = r["revenue"] - r["cost"]
        out.append({"name": r["name"], "jobs": len(r["jobs"]), "revenue": str(r["revenue"]), "cost": str(r["cost"]), "gross_profit": str(gp), "gp_percent": str((gp * 100 / r["revenue"]).quantize(Decimal("0.1"))) if r["revenue"] else "0.0"})
    tr, tc = sum((r["revenue"] for r in rows.values()), ZERO), sum((r["cost"] for r in rows.values()), ZERO)
    return {"date_from": date_from, "date_to": date_to, "rows": out, "totals": {"jobs": sum(len(r["jobs"]) for r in rows.values()), "revenue": str(tr), "cost": str(tc), "gross_profit": str(tr - tc)}}


@router.get("/branch")
def branch_analysis(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    return _by_job_field(db, _tenant(user), "branch", date_from, date_to)


@router.get("/salesperson")
def salesperson_performance(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("billing.read")), db: Session = Depends(get_db)):
    return _by_job_field(db, _tenant(user), "salesperson", date_from, date_to)
