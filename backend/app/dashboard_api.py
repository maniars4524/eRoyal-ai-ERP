from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_current_user, has_permission
from .billing import outstanding
from .db import get_db
from .models import Customer, Document, Shipment, User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

CLOSED = {"Closed", "Cancelled"}
PENDING = {"Draft", "Open"}
TYPE_ORDER = ["Sea Export", "Sea Import", "Air Export", "Air Import", "Road / Transport"]


def _has(user: User, code: str) -> bool:
    return has_permission(user, code)


def _bucket(job: Shipment) -> str:
    if job.mode in ("Road", "Rail"):
        return "Road / Transport"
    return f"{job.mode} {job.direction}" if job.direction in ("Export", "Import") else f"{job.mode} {job.direction}"


def _month_keys(today: date, n: int = 6) -> list[str]:
    keys, y, m = [], today.year, today.month
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(keys))


def fiscal_label(today: date) -> str:
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}–{str(start + 1)[2:]}"


@router.get("/summary")
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Tenant-scoped dashboard numbers computed from the live database (no placeholders)."""
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The dashboard is tenant-scoped; Super Admin has no company dashboard")
    org_id = user.organization_id
    today = date.today()
    month_start = today.replace(day=1)
    can_jobs, can_bill = _has(user, "shipment.read"), _has(user, "billing.read")

    jobs = list(db.scalars(select(Shipment).where(Shipment.organization_id == org_id))) if can_jobs else []
    names = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.organization_id == org_id))} if can_jobs else {}
    live = [j for j in jobs if j.status != "Cancelled"]
    active = [j for j in live if j.status not in CLOSED]

    matrix = {label: {"active": 0, "closed_mtd": 0, "pending": 0} for label in TYPE_ORDER}
    for j in live:
        row = matrix.setdefault(_bucket(j), {"active": 0, "closed_mtd": 0, "pending": 0})
        if j.status not in CLOSED:
            row["active"] += 1
            if j.status in PENDING:
                row["pending"] += 1
        elif j.status == "Closed" and j.updated_at and j.updated_at.date() >= month_start:
            row["closed_mtd"] += 1
    mix = Counter(_bucket(j) for j in live)
    lanes = Counter(f"{j.pol or '—'} → {j.pod or '—'}" for j in live if j.pol or j.pod)

    months = _month_keys(today)
    jobs_by_month = Counter(j.created_at.strftime("%Y-%m") for j in live if j.created_at)

    docs = list(db.scalars(select(Document).where(Document.organization_id == org_id, Document.status == "POSTED"))) if can_bill else []
    mode_of = {j.id: j.mode for j in jobs}
    revenue_by_month: dict[str, Decimal] = defaultdict(Decimal)
    rev_mode: dict[str, Decimal] = defaultdict(Decimal)
    cost_mode: dict[str, Decimal] = defaultdict(Decimal)
    for d in docs:
        sign = -1 if d.doc_type == "credit_note" else 1
        if d.doc_type in ("invoice", "credit_note", "debit_note"):
            revenue_by_month[d.doc_date.strftime("%Y-%m")] += sign * d.subtotal
            if d.doc_date >= month_start:
                rev_mode[mode_of.get(d.shipment_id, "Other")] += sign * d.subtotal
        elif d.doc_type in ("purchase_invoice", "purchase_credit_note"):
            if d.doc_date >= month_start:
                cost_mode[mode_of.get(d.shipment_id, "Other")] += (-1 if d.doc_type == "purchase_credit_note" else 1) * d.subtotal
    revenue_mtd = sum(rev_mode.values(), Decimal(0))
    cost_mtd = sum(cost_mode.values(), Decimal(0))
    gp_mtd = revenue_mtd - cost_mtd

    receivable = [d for d in docs if d.doc_type in ("invoice", "debit_note") and outstanding(d) > 0]
    ageing = {"not_due": Decimal(0), "d1_30": Decimal(0), "d31_60": Decimal(0), "d61_90": Decimal(0), "d90_plus": Decimal(0)}
    overdue_by_party: dict[str, Decimal] = defaultdict(Decimal)
    overdue_count = 0
    for d in receivable:
        left, due = outstanding(d), d.due_date or d.doc_date
        late = (today - due).days
        key = "not_due" if late <= 0 else "d1_30" if late <= 30 else "d31_60" if late <= 60 else "d61_90" if late <= 90 else "d90_plus"
        ageing[key] += left
        if late > 0:
            overdue_by_party[d.party_name] += left
            overdue_count += 1
    ar_total = sum(ageing.values(), Decimal(0))
    drafts = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == org_id, Document.status == "DRAFT")) if can_bill else 0

    soon = today + timedelta(days=7)
    eta_soon = [j for j in active if j.eta and today <= j.eta <= soon]
    recent = sorted(live, key=lambda j: j.created_at or today, reverse=True)[:5]
    s = str
    return {
        "generated_for": today.isoformat(), "fiscal_year": fiscal_label(today),
        "jobs": {"active": len(active), "total": len(live), "by_status": dict(Counter(j.status for j in active)), "matrix": matrix, "mix": [{"label": k, "count": v} for k, v in mix.most_common()], "top_lanes": [{"lane": k, "count": v} for k, v in lanes.most_common(5)],
                 "customs_pending": sum(1 for j in active if j.job_type == "Customs"), "recent": [{"job_number": j.job_number, "customer": names.get(j.customer_id) or j.consignee or "—", "mode": j.mode, "direction": j.direction, "status": j.status, "route": " → ".join(x for x in (j.pol, j.pod) if x) or "—"} for j in recent]},
        "monthly": [{"month": k, "label": date(int(k[:4]), int(k[5:]), 1).strftime("%b"), "jobs": jobs_by_month.get(k, 0), "revenue": s(revenue_by_month.get(k, Decimal(0)))} for k in months],
        "money": {"revenue_mtd": s(revenue_mtd), "cost_mtd": s(cost_mtd), "gp_mtd": s(gp_mtd), "gp_pct": s(round(gp_mtd / revenue_mtd * 100, 1)) if revenue_mtd else "0", "revenue_by_mode": {k: s(v) for k, v in rev_mode.items()}, "gp_by_mode": {k: s(rev_mode.get(k, Decimal(0)) - cost_mode.get(k, Decimal(0))) for k in set(rev_mode) | set(cost_mode)},
                  "ar_outstanding": s(ar_total), "ageing": {k: s(v) for k, v in ageing.items()}, "top_overdue": [{"party": k, "amount": s(v)} for k, v in sorted(overdue_by_party.items(), key=lambda kv: -kv[1])[:5]]},
        "alerts": {"overdue_invoices": overdue_count, "overdue_amount": s(sum(overdue_by_party.values(), Decimal(0))), "draft_documents": drafts or 0, "jobs_arriving_7d": [{"job_number": j.job_number, "eta": j.eta.isoformat(), "route": " → ".join(x for x in (j.pol, j.pod) if x) or "—"} for j in eta_soon[:10]]},
        "access": {"jobs": can_jobs, "billing": can_bill},
    }
