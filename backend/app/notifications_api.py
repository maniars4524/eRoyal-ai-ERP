"""Notification centre: one list of everything that needs a person's attention, gathered from the modules by small providers.

Each provider looks only at real records, only for what the signed-in user may see (their permissions and job access), and returns plain items. Adding a new kind of notification later means
adding one provider function to PROVIDERS; the dashboard running message and the "view all" window pick it up with no other change.
Item: {id, category, severity (0 critical, 1 action needed, 2 information, 3 low), title, text, at, link{go, jobNo?, tab?}}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import shipment_clause
from .auth import get_current_user, has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import ContainerMovement, Document, InternalMessage, MasterRecord, NotificationSetting, Quotation, Role, Shipment, User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
CATEGORIES = ["Shipments", "Approvals", "Payments", "People", "Messages"]
DEFAULTS = {
    "routing": {"etd_lead_value": 5, "etd_lead_unit": "days", "eta_lead_value": 5, "eta_lead_unit": "days", "planned_lead_days": 14, "changed_days": 3},
    "payments": {"due_lead_days": 7},
    "approvals": {"escalate_after_days": 3},
    "birthdays": {"lead_days": 7},
    "display": {"ticker_speed": "normal"},                                           # the running message on the dashboard: slow | normal | fast
    "audience": {c: {"enabled": True, "roles": []} for c in CATEGORIES},          # roles empty = everyone who may see that kind of record
}
LIMITS = {("routing", "etd_lead_value"): (1, 720), ("routing", "eta_lead_value"): (1, 720), ("routing", "planned_lead_days"): (1, 90), ("routing", "changed_days"): (1, 30),
          ("payments", "due_lead_days"): (0, 60), ("approvals", "escalate_after_days"): (0, 60), ("birthdays", "lead_days"): (0, 30)}


def settings_for(db: Session, org_id: int) -> dict:
    row = db.get(NotificationSetting, org_id)
    saved = (row.data if row else None) or {}
    out = {k: (dict(v) if k != "audience" else {c: dict(x) for c, x in v.items()}) for k, v in DEFAULTS.items()}
    for k, v in saved.items():
        if k == "audience":
            for c, x in (v or {}).items():
                if c in out["audience"] and isinstance(x, dict):
                    out["audience"][c].update({"enabled": bool(x.get("enabled", True)), "roles": [str(r) for r in (x.get("roles") or [])][:30]})
        elif k in out and isinstance(v, dict):
            out[k].update({a: b for a, b in v.items() if a in out[k]})
    return out


def _lead(value, unit) -> timedelta:
    return timedelta(hours=value) if unit == "hours" else timedelta(days=value)


def _receives(user: User, cfg: dict, category: str) -> bool:
    a = cfg["audience"].get(category, {"enabled": True, "roles": []})
    if not a["enabled"]:
        return False
    if not a["roles"] or user.is_super_admin:
        return True
    mine = {r.name.lower() for r in user.roles}
    return any("admin" in r for r in mine) or any(r.lower() in mine for r in a["roles"])             # company administrators always receive everything


def item(key, category, severity, title, text="", at=None, link=None) -> dict:
    return {"id": f"{category}:{key}", "category": category, "severity": severity, "title": title, "text": text, "at": at, "link": link or {}}


def _money(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v or "")


def _visible_jobs(db: Session, user: User):
    """A document of a job is shown only when the user may see that job."""
    clause = shipment_clause(db, user)
    if clause is None:
        return None
    return select(Shipment.job_number).where(Shipment.organization_id == user.organization_id, clause)


def p_routing(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    if not has_permission(user, "shipment.read"):
        return []
    from .routing_api import compute_alerts

    r = cfg["routing"]
    sev = {"OVERDUE": 0, "FOLLOW_UP": 1, "PLANNED": 2, "ETD": 2, "ETA": 2, "CHANGED": 3}
    out = []
    for a in compute_alerts(user, db, _lead(r["etd_lead_value"], r["etd_lead_unit"]), _lead(r["eta_lead_value"], r["eta_lead_unit"]), r["planned_lead_days"], r["changed_days"]):
        what = ("AIR" if a["kind"] == "AIR" else "SEA") + ": Job " + a["job_number"] + (" - " + a["carrier"] if a["carrier"] else "") + (" - " + ("Flight " if a["kind"] == "AIR" else "Voyage ") + a["voyage"] if a["voyage"] else "")
        out.append(item(f"routing:{a['leg_id']}", "Shipments", min(sev.get(f, 3) for f in a["flags"]), what, a["message"] + (f" - {a['customer']}" if a["customer"] else ""), a["etd"] or a["eta"],
                        {"go": "job-detail", "jobNo": a["job_number"], "tab": "routing"}))
    return out


def p_invoices(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    if not has_permission(user, "billing.read"):
        return []
    q = select(Document).where(Document.organization_id == user.organization_id, Document.doc_type == "invoice", Document.status.in_(("DRAFT", "CONFIRMED")))
    vis = _visible_jobs(db, user)
    if vis is not None:
        q = q.where(Document.job_number.in_(vis))
    out = []
    for d in db.scalars(q.order_by(Document.doc_date).limit(60)):
        age = (today - d.doc_date).days if d.doc_date else 0
        what = "Proforma invoice waiting for confirmation" if d.status == "DRAFT" else "Proforma confirmed - tax invoice not generated yet"
        ref = (d.header or {}).get("proforma_no") or d.doc_number or "draft"
        out.append(item(f"inv:{d.id}", "Approvals", 1 if age >= cfg["approvals"]["escalate_after_days"] else 2, f"Invoice approval pending: {ref}", f"{what} - {d.party_name or ''} - {_money(d.grand_total)}" + (f" - Job {d.job_number}" if d.job_number else ""),
                        datetime.combine(d.doc_date, datetime.min.time(), tzinfo=timezone.utc) if d.doc_date else None, {"go": "invoice-list"}))
    return out


def p_quotations(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    if not has_permission(user, "quote.approve"):
        return []
    out = []
    for qt in db.scalars(select(Quotation).where(Quotation.organization_id == user.organization_id, Quotation.approval_status == "SUBMITTED").order_by(Quotation.id).limit(60)):
        out.append(item(f"qt:{qt.id}", "Approvals", 1, f"Quotation approval pending: {qt.number or 'draft'}", f"{qt.party_name or ''} - submitted for approval", qt.updated_at if hasattr(qt, "updated_at") else None, {"go": "quotation"}))
    return out


def p_containers(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    if not has_permission(user, "container.approve"):
        return []
    n = db.scalar(select(func.count()).select_from(ContainerMovement).where(ContainerMovement.organization_id == user.organization_id, ContainerMovement.status == "DRAFT")) or 0
    return [item("cm", "Approvals", 1, f"Container movements waiting for approval: {n}", "Approve or reject them in Container Movement Management", None, {"go": "inv-movements"})] if n else []


def p_purchase_bills(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    if not has_permission(user, "billing.read"):
        return []
    q = select(Document).where(Document.organization_id == user.organization_id, Document.doc_type == "purchase_invoice", Document.status == "POSTED", Document.due_date.is_not(None))
    vis = _visible_jobs(db, user)
    if vis is not None:
        q = q.where((Document.job_number.is_(None)) | Document.job_number.in_(vis))
    out = []
    for d in db.scalars(q.order_by(Document.due_date).limit(200)):
        owing = float(d.grand_total or 0) - float(d.paid_amount or 0) - float(getattr(d, "credited_amount", 0) or 0)
        if owing <= 0.005:
            continue
        days = (d.due_date - today).days
        if days > cfg["payments"]["due_lead_days"]:
            continue
        when = f"overdue by {-days} day(s)" if days < 0 else "due today" if days == 0 else f"due in {days} day(s)"
        out.append(item(f"pb:{d.id}", "Payments", 0 if days < 0 else 1, f"Purchase bill {when}: {d.doc_number or d.id}", f"{d.party_name or ''} - balance {_money(owing)} - due {d.due_date.strftime('%d %b %Y')}",
                        datetime.combine(d.due_date, datetime.min.time(), tzinfo=timezone.utc), {"go": "vendor-bill"}))
    return out


def p_birthdays(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    out = []
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == user.organization_id, MasterRecord.master_type == "employee", MasterRecord.status == "ACTIVE")):
        raw = str((r.data or {}).get("date_of_birth") or (r.data or {}).get("dob") or "")[:10]
        try:
            dob = date.fromisoformat(raw)
        except ValueError:
            continue
        try:
            nxt = dob.replace(year=today.year)
        except ValueError:
            nxt = date(today.year, 3, 1)
        if nxt < today:
            nxt = nxt.replace(year=today.year + 1) if not (nxt.month == 2 and nxt.day == 29) else date(today.year + 1, 3, 1)
        d = (nxt - today).days
        if d <= cfg["birthdays"]["lead_days"]:
            out.append(item(f"bd:{r.id}", "People", 2 if d == 0 else 3, f"Birthday {'today' if d == 0 else 'in ' + str(d) + ' day(s)'}: {r.name}", nxt.strftime("%d %B"), datetime.combine(nxt, datetime.min.time(), tzinfo=timezone.utc)))
    return out


def p_messages(db: Session, user: User, today: date, cfg: dict) -> list[dict]:
    names = {u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    out = []
    for m in db.scalars(select(InternalMessage).where(InternalMessage.organization_id == user.organization_id, InternalMessage.is_active.is_(True)).order_by(InternalMessage.id.desc()).limit(50)):
        if m.expires_on and m.expires_on < today:
            continue
        if m.audience_roles and not user.is_super_admin and not ({r.name.lower() for r in user.roles} & {x.lower() for x in m.audience_roles} or any("admin" in r.name.lower() for r in user.roles)):
            continue
        out.append(item(f"msg:{m.id}", "Messages", 2, m.title, (m.body or "") + (f" - from {names.get(m.created_by)}" if names.get(m.created_by) else ""), m.created_at))
    return out


PROVIDERS = [p_routing, p_invoices, p_quotations, p_containers, p_purchase_bills, p_birthdays, p_messages]


@router.get("")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything waiting for this user, most urgent first. Each provider decides what the user may see."""
    if user.organization_id is None:
        return {"items": [], "counts": {}, "categories": CATEGORIES}
    today = datetime.now(timezone.utc).date()
    cfg = settings_for(db, user.organization_id)
    items: list[dict] = []
    for prov in PROVIDERS:
        try:
            items.extend(x for x in prov(db, user, today, cfg) if _receives(user, cfg, x["category"]))
        except Exception:                              # one broken source must never hide the others
            db.rollback()
    items.sort(key=lambda x: (x["severity"], str(x["at"] or "")))
    counts = {c: sum(1 for x in items if x["category"] == c) for c in CATEGORIES}
    return {"items": items[:400], "counts": counts, "categories": CATEGORIES, "total": len(items), "ticker_speed": cfg["display"]["ticker_speed"]}


class MessageIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(default="", max_length=1000)
    expires_on: date | None = None
    audience_roles: list[str] = Field(default_factory=list, max_length=30)


@router.post("/messages", status_code=201)
def post_message(body: MessageIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """A message from the company to all its users (shown in the notification centre until it expires or is withdrawn)."""
    m = InternalMessage(organization_id=user.organization_id, title=body.title.strip(), body=body.body.strip(), expires_on=body.expires_on, audience_roles=[r.strip() for r in body.audience_roles if r.strip()] or None, created_by=user.id)
    db.add(m)
    db.flush()
    record_audit(db, "MESSAGE_POSTED", "NOTIFICATION", user, user.organization_id, "InternalMessage", str(m.id), {"title": m.title})
    db.commit()
    return {"id": m.id}


@router.delete("/messages/{message_id}")
def withdraw_message(message_id: int, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    m = db.get(InternalMessage, message_id)
    if not m or m.organization_id != user.organization_id:
        raise HTTPException(404, "Message not found")
    m.is_active = False
    record_audit(db, "MESSAGE_WITHDRAWN", "NOTIFICATION", user, user.organization_id, "InternalMessage", str(m.id), {"title": m.title})
    db.commit()
    return {"withdrawn": m.id}


# ---------------------------------------------------------------- settings (Settings > Notification Settings)
@router.get("/settings")
def get_settings(user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    roles = sorted({r.name for r in db.scalars(select(Role).where((Role.organization_id == user.organization_id) | (Role.organization_id.is_(None))))})
    return {"settings": settings_for(db, user.organization_id), "defaults": DEFAULTS, "roles": roles, "categories": CATEGORIES}


class SettingsIn(BaseModel):
    routing: dict | None = None
    payments: dict | None = None
    approvals: dict | None = None
    birthdays: dict | None = None
    display: dict | None = None
    audience: dict | None = None


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    cur = settings_for(db, user.organization_id)
    for group in ("routing", "payments", "approvals", "birthdays", "display"):
        incoming = getattr(body, group)
        if incoming is None:
            continue
        for k, v in incoming.items():
            if k not in cur[group]:
                raise HTTPException(422, f"Unknown setting {group}.{k}")
            if k == "ticker_speed":
                if v not in ("slow", "normal", "fast"):
                    raise HTTPException(422, "The running message speed must be slow, normal or fast")
            elif k.endswith("_unit"):
                if v not in ("days", "hours"):
                    raise HTTPException(422, "The unit must be days or hours")
            else:
                lo, hi = LIMITS[(group, k)]
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{k.replace('_', ' ')} must be a whole number")
                if not lo <= v <= hi:
                    raise HTTPException(422, f"{k.replace('_', ' ')} must be between {lo} and {hi}")
            cur[group][k] = v
    if body.audience is not None:
        for c, x in body.audience.items():
            if c not in CATEGORIES or not isinstance(x, dict):
                raise HTTPException(422, f"Unknown notification group {c}")
            cur["audience"][c] = {"enabled": bool(x.get("enabled", True)), "roles": [str(r).strip() for r in (x.get("roles") or []) if str(r).strip()][:30]}
    row = db.get(NotificationSetting, user.organization_id)
    if row is None:
        db.add(NotificationSetting(organization_id=user.organization_id, data=cur, updated_by=user.id))
    else:
        row.data, row.updated_by = cur, user.id
    record_audit(db, "NOTIFICATION_SETTINGS_CHANGED", "NOTIFICATION", user, user.organization_id, "NotificationSetting", str(user.organization_id), {})
    db.commit()
    return {"settings": cur}
