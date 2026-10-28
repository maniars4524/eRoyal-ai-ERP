from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_current_user, has_permission
from .billing import outstanding
from .db import get_db
from .models import AuditLog, CreditFacility, CreditRequest, CrmRecord, Customer, Document, Quotation, Shipment, User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

CLOSED = {"Closed", "Cancelled"}
PENDING_STAGES = {"Draft", "Reopened"}   # an active job that still needs action: not yet confirmed, or reopened


def is_closed(job: Shipment) -> bool:
    return job.closure_status == "CLOSED" or job.status == "Closed"


def closed_this_month(job: Shipment, month_start: date) -> bool:
    when = job.last_closed_at or job.updated_at
    return is_closed(job) and bool(when) and when.date() >= month_start


def is_pending(job: Shipment) -> bool:
    return not is_closed(job) and job.job_stage in PENDING_STAGES
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
    active = [j for j in live if not is_closed(j)]

    freight_jobs = [j for j in live if j.job_type not in ("Customs", "Transport", "Warehouse")]          # a job is counted in one category only: Freight, CHA or Transport
    matrix = {label: {"active": 0, "closed_mtd": 0, "pending": 0} for label in TYPE_ORDER}
    for j in freight_jobs:
        row = matrix.setdefault(_bucket(j), {"active": 0, "closed_mtd": 0, "pending": 0})
        if not is_closed(j):
            row["active"] += 1
            if is_pending(j):
                row["pending"] += 1
        elif closed_this_month(j, month_start):
            row["closed_mtd"] += 1
    mix = Counter(_bucket(j) for j in freight_jobs)
    lanes = Counter(f"{j.pol or '—'} → {j.pod or '—'}" for j in live if j.pol or j.pod)

    def counts(rows):
        return {"active": sum(1 for j in rows if not is_closed(j)), "closed_mtd": sum(1 for j in rows if closed_this_month(j, month_start)), "pending": sum(1 for j in rows if is_pending(j))}
    cha_jobs = [j for j in live if j.job_type == "Customs"]
    trp_jobs = [j for j in live if j.job_type == "Transport"]
    st = lambda j: j.customs_status or "CREATED"
    # every CHA job sits in exactly one stage row, so the rows add up to the total CHA jobs; Import / Export is only a split of that total
    cha_rows = [("Total CHA jobs", {}, cha_jobs),
                ("Created / documentation pending", {"group": "draft"}, [j for j in cha_jobs if st(j) in ("CREATED", "DOCUMENTATION", "DATA ENTRY")]), ("Ready to file with customs", {"group": "pending"}, [j for j in cha_jobs if st(j) in ("VALIDATED", "CHECKLIST")]),
                ("BE / SB sent to customs", {"group": "sent"}, [j for j in cha_jobs if st(j) == "CUSTOMS FILED"]), ("BE / SB received (customs response)", {"group": "received"}, [j for j in cha_jobs if st(j) == "CUSTOMS RESPONSE"]),
                ("Examination / assessment pending", {"group": "examination"}, [j for j in cha_jobs if st(j) == "EXAMINATION"]), ("OOC / LEO received, delivery pending", {"group": "cleared"}, [j for j in cha_jobs if st(j) == "OOC / LEO CLEARED"]),
                ("Delivered / closed", {"group": "completed"}, [j for j in cha_jobs if st(j) in ("DELIVERED", "CLOSED")])]
    trp_rows = [("Transport jobs (all)", {}, trp_jobs), ("Export transportation", {"direction": "Export"}, [j for j in trp_jobs if j.direction == "Export"]), ("Import delivery", {"direction": "Import"}, [j for j in trp_jobs if j.direction == "Import"]),
                ("Local / domestic transport", {"direction": "Domestic"}, [j for j in trp_jobs if j.direction not in ("Export", "Import")]), ("In transit", {"stage": "On Transit"}, [j for j in trp_jobs if j.job_stage == "On Transit"])]
    categories = {"freight": counts(freight_jobs), "cha": counts(cha_jobs), "transport": counts(trp_jobs)}
    cha_breakdown = [{"label": l, "filter": f, **counts(r)} for l, f, r in cha_rows]
    trp_breakdown = [{"label": l, "filter": f, **counts(r)} for l, f, r in trp_rows]

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
        "jobs": {"active": len(active), "total": len(live), "categories": categories, "cha_breakdown": cha_breakdown, "cha_split": {"Import": sum(1 for j in cha_jobs if j.direction == "Import" and not is_closed(j)), "Export": sum(1 for j in cha_jobs if j.direction == "Export" and not is_closed(j))}, "transport_breakdown": trp_breakdown, "by_status": dict(Counter(j.status for j in active)), "matrix": matrix, "mix": [{"label": k, "count": v} for k, v in mix.most_common()], "top_lanes": [{"lane": k, "count": v} for k, v in lanes.most_common(5)],
                 "customs_pending": sum(1 for j in active if j.job_type == "Customs"), "recent": [{"job_number": j.job_number, "customer": names.get(j.customer_id) or j.consignee or "—", "mode": j.mode, "direction": j.direction, "status": j.status, "route": " → ".join(x for x in (j.pol, j.pod) if x) or "—"} for j in recent]},
        "monthly": [{"month": k, "label": date(int(k[:4]), int(k[5:]), 1).strftime("%b"), "jobs": jobs_by_month.get(k, 0), "revenue": s(revenue_by_month.get(k, Decimal(0)))} for k in months],
        "money": {"revenue_mtd": s(revenue_mtd), "cost_mtd": s(cost_mtd), "gp_mtd": s(gp_mtd), "gp_pct": s(round(gp_mtd / revenue_mtd * 100, 1)) if revenue_mtd else "0", "revenue_by_mode": {k: s(v) for k, v in rev_mode.items()}, "gp_by_mode": {k: s(rev_mode.get(k, Decimal(0)) - cost_mode.get(k, Decimal(0))) for k in set(rev_mode) | set(cost_mode)},
                  "ar_outstanding": s(ar_total), "ageing": {k: s(v) for k, v in ageing.items()}, "top_overdue": [{"party": k, "amount": s(v)} for k, v in sorted(overdue_by_party.items(), key=lambda kv: -kv[1])[:5]]},
        "alerts": {"overdue_invoices": overdue_count, "overdue_amount": s(sum(overdue_by_party.values(), Decimal(0))), "draft_documents": drafts or 0, "jobs_arriving_7d": [{"job_number": j.job_number, "eta": j.eta.isoformat(), "route": " → ".join(x for x in (j.pol, j.pod) if x) or "—"} for j in eta_soon[:10]]},
        "access": {"jobs": can_jobs, "billing": can_bill},
    }


# ---------------------------------------------------------------- the dashboard side lists (tasks, approvals, notifications, activity)
def _name(db: Session, cache: dict, uid: int | None) -> str:
    if not uid:
        return "System"
    if uid not in cache:
        u = db.get(User, uid)
        cache[uid] = ((u.first_name + " " + (u.last_name or "")).strip() or u.username or u.email) if u else "—"
    return cache[uid]


def _words(action: str) -> str:
    return action.replace("_", " ").capitalize()


@router.get("/side")
def side_lists(limit: int = Query(default=6, ge=1, le=300), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What needs THIS user: today's tasks (follow-ups), approvals waiting for them, news about what they submitted, and recent activity. Tenant-scoped; nothing is shared across companies."""
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The dashboard is tenant-scoped")
    org_id, today, names = user.organization_id, date.today(), {}
    out = {"tasks": [], "approvals": [], "notifications": [], "activity": []}

    # tasks: this user's open follow-ups that are due today or overdue
    for r in db.scalars(select(CrmRecord).where(CrmRecord.organization_id == org_id, CrmRecord.kind == "followup", CrmRecord.created_by == user.id).order_by(CrmRecord.id.desc())):
        d = r.data or {}
        due = str(d.get("due") or "")[:10]
        if d.get("done") or not due or due > today.isoformat():
            continue
        out["tasks"].append({"ref": r.ref, "title": f"{d.get('type') or 'Follow-up'} — {d.get('relatedLabel') or r.company or ''}".strip(" —"), "due": due, "overdue": due < today.isoformat(), "notes": d.get("notes") or "", "go": "crm-engagement"})
    out["tasks"].sort(key=lambda t: t["due"])

    # approvals waiting for this user's decision
    if _has(user, "quote.approve"):
        for q in db.scalars(select(Quotation).where(Quotation.organization_id == org_id, Quotation.status == "DRAFT", Quotation.approval_status == "SUBMITTED").order_by(Quotation.submitted_at)):
            out["approvals"].append({"type": "Quotation", "ref": q.number or f"Draft #{q.id}", "party": q.party_name, "by": _name(db, names, q.prepared_by), "at": q.submitted_at, "own": q.prepared_by == user.id, "id": q.id, "go": "quotation", "open": "quotation"})
    if _has(user, "credit.approve"):
        for f in db.scalars(select(CreditFacility).where(CreditFacility.organization_id == org_id, CreditFacility.status.in_(("SUBMITTED", "UNDER_REVIEW"))).order_by(CreditFacility.submitted_at)):
            c = db.get(Customer, f.customer_id)
            out["approvals"].append({"type": "Credit facility", "ref": f"Credit application #{f.id}", "party": c.name if c else "—", "by": _name(db, names, f.requested_by), "at": f.submitted_at, "own": f.requested_by == user.id, "id": f.customer_id, "go": "org-list", "open": "credit"})
        for r in db.scalars(select(CreditRequest).where(CreditRequest.organization_id == org_id, CreditRequest.status == "PENDING").order_by(CreditRequest.id)):
            c = db.get(Customer, r.customer_id)
            out["approvals"].append({"type": "Credit request", "ref": f"Credit request #{r.id}", "party": c.name if c else "—", "by": _name(db, names, r.requested_by), "at": r.created_at if hasattr(r, "created_at") else None, "own": r.requested_by == user.id, "id": r.customer_id, "go": "org-list", "open": "credit"})

    # news about what this user submitted: decided by someone else in the last 14 days
    since = today - timedelta(days=14)
    for q in db.scalars(select(Quotation).where(Quotation.organization_id == org_id, Quotation.prepared_by == user.id, Quotation.approval_status.in_(("APPROVED", "REJECTED")), Quotation.approved_at.is_not(None)).order_by(Quotation.approved_at.desc()).limit(40)):
        if q.approved_at.date() >= since and q.approved_by != user.id:
            out["notifications"].append({"text": f"Quotation {q.number or '#' + str(q.id)} was {q.approval_status.lower()} by {_name(db, names, q.approved_by)}", "at": q.approved_at, "kind": "ok" if q.approval_status == "APPROVED" else "bad", "id": q.id, "open": "quotation", "go": "quotation"})
    for r in db.scalars(select(CreditRequest).where(CreditRequest.organization_id == org_id, CreditRequest.requested_by == user.id, CreditRequest.status != "PENDING", CreditRequest.decided_at.is_not(None)).order_by(CreditRequest.decided_at.desc()).limit(20)):
        if r.decided_at.date() >= since and r.decided_by != user.id:
            out["notifications"].append({"text": f"Credit request #{r.id} was {r.status.lower()} by {_name(db, names, r.decided_by)}", "at": r.decided_at, "kind": "ok" if r.status == "APPROVED" else "bad", "id": r.customer_id, "open": "credit", "go": "org-list"})
    out["notifications"].sort(key=lambda n: n["at"], reverse=True)

    # recent activity of the company (the audit trail, newest first)
    for a in db.scalars(select(AuditLog).where(AuditLog.organization_id == org_id, AuditLog.module != "AUTHENTICATION").order_by(AuditLog.id.desc()).limit(limit if limit > 6 else 40)):
        nv = a.new_value or {}
        ref = nv.get("number") or nv.get("job_number") or nv.get("code") or nv.get("name") or ""
        out["activity"].append({"text": _words(a.action) + (f" — {ref}" if ref else ""), "by": _name(db, names, a.user_id), "at": a.created_at, "module": a.module})
    out["counts"] = {k: len(out[k]) for k in ("tasks", "approvals", "notifications", "activity")}
    for k in ("tasks", "approvals", "notifications", "activity"):
        out[k] = out[k][:limit]
    return out
