"""License Management API: companies, licenses, renewals, users, usage, reminders and the license audit trail. Every route requires the owner company's license super admin (server-side)."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import config
from . import license_mgmt as lm
from .auth_api import create_company, record_audit
from .db import get_db
from .models import AuditLog, LicenseNotification, LicenseRenewal, Organization, Role, User, UserUsage
from .schemas_auth import OrganizationAdminCreate

router = APIRouter(prefix="/api/license-mgmt", tags=["license management"])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _org(db: Session, org_id: int) -> Organization:
    org = db.get(Organization, org_id)
    if org is None or org.code == "RTS-PLATFORM":
        raise HTTPException(404, "Company not found")
    return org


def _clean_modules(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    bad = [m for m in v if m not in lm.MODULES]
    if bad:
        raise HTTPException(422, "Unknown module: " + ", ".join(bad))
    return sorted(set(v), key=lm.MODULES.index)


def _rows(db: Session, orgs: list[Organization]) -> list[dict]:
    ids = [o.id for o in orgs]
    users = Counter(uid for uid in db.scalars(select(User.organization_id).where(User.organization_id.in_(ids), User.status == "ACTIVE", User.is_super_admin.is_(False)))) if ids else Counter()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=config.SESSION_ACTIVE_MINUTES)
    sess = Counter(uid for uid in db.scalars(select(User.organization_id).where(User.organization_id.in_(ids), User.session_id.is_not(None), User.session_seen_at >= cutoff))) if ids else Counter()
    track = config.SINGLE_SESSION
    now = datetime.now(timezone.utc)
    online = Counter(oid for oid in db.scalars(select(UserUsage.organization_id).where(UserUsage.organization_id.in_(ids), UserUsage.day == now.date(), UserUsage.last_seen_at >= now - timedelta(minutes=lm.ONLINE_MINUTES)))) if ids else Counter()
    secs = Counter()
    if ids:
        for oid, total in db.execute(select(UserUsage.organization_id, func.sum(UserUsage.active_seconds)).where(UserUsage.organization_id.in_(ids), UserUsage.day >= now.date() - timedelta(days=6)).group_by(UserUsage.organization_id)):
            secs[oid] = int(total or 0)
    rows = [lm.summary_row(o, users.get(o.id, 0), sess.get(o.id, 0) if track else None) for o in orgs]
    for r in rows:
        r["online_now"], r["online_minutes_7d"] = online.get(r["id"], 0), secs.get(r["id"], 0) // 60
    return rows


# ---------------------------------------------------------------- dashboard
@router.get("/summary")
def summary(user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    orgs = [o for o in db.scalars(select(Organization).order_by(Organization.id)) if o.code != "RTS-PLATFORM"]
    rows = _rows(db, orgs)
    by = Counter(r["status"] for r in rows)
    capacity = sum(r["max_users"] or 0 for r in rows)
    unlimited = sum(1 for r in rows if r["max_users"] is None)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    active_users = len(set(db.scalars(select(AuditLog.user_id).where(AuditLog.created_at >= since, AuditLog.user_id.is_not(None), AuditLog.module == "AUTHENTICATION", AuditLog.action == "LOGIN"))))
    return {"companies": len(rows), "licenses": sum(1 for r in rows if r["license_number"]), "active": by["ACTIVE"] + by["EXPIRING"], "expiring_soon": by["EXPIRING"], "expired": by["EXPIRED"], "suspended": by["SUSPENDED"], "cancelled": by["CANCELLED"],
            "licensed_user_capacity": capacity, "companies_without_user_limit": unlimited, "enabled_users": sum(r["users"] for r in rows), "distinct_users_signed_in_30d": active_users,
            "active_sessions": sum(r["sessions"] or 0 for r in rows) if config.SINGLE_SESSION else None, "session_tracking": config.SINGLE_SESSION, "open_reminders": db.scalar(select(func.count()).select_from(LicenseNotification).where(LicenseNotification.status == "OPEN")) or 0}


@router.get("/companies")
def companies(q: str | None = Query(default=None, max_length=100), status_filter: str | None = Query(default=None, alias="status", pattern="^(ACTIVE|EXPIRING|EXPIRED|SUSPENDED|CANCELLED)$"), limit: int = Query(default=20, ge=1, le=200),
              offset: int = Query(default=0, ge=0), user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    query = select(Organization).where(Organization.code != "RTS-PLATFORM")
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Organization.name.ilike(like), Organization.code.ilike(like), Organization.license_number.ilike(like)))
    orgs = list(db.scalars(query.order_by(Organization.id)))
    rows = _rows(db, orgs)
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    return {"total": len(rows), "items": rows[offset:offset + limit], "limit": limit, "offset": offset}


# ---------------------------------------------------------------- create / edit
class CompanyIn(BaseModel):
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    country: str = Field(default="India", max_length=80)
    contact_person: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=400)
    admin_email: str = Field(min_length=5, max_length=254)
    admin_username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    admin_password: str = Field(min_length=1, max_length=256)
    admin_first_name: str = Field(min_length=1, max_length=80)
    admin_last_name: str = Field(default="", max_length=80)
    license_start: date | None = None
    license_expiry: date | None = None
    renewal_cycle: str = Field(default="YEARLY", pattern="^(MONTHLY|YEARLY)$")
    maintenance_plan: str | None = Field(default=None, pattern="^(AMC|MMC)$")
    max_users: int | None = Field(default=None, ge=1, le=100000)
    max_sessions: int | None = Field(default=None, ge=1, le=100000)
    modules: list[str] | None = None
    license_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("contact_email", "admin_email")
    @classmethod
    def _mail(cls, v):
        if v and not EMAIL.match(v):
            raise ValueError("Email ID is not valid")
        return v


@router.post("/companies", status_code=201)
def create_company_license(body: CompanyIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Create the company, its first administrator and its license in ONE transaction (nothing is left half-made if any step fails)."""
    start = body.license_start or lm.today_utc()
    expiry = body.license_expiry or lm.add_months(start, 1 if body.renewal_cycle == "MONTHLY" else 12)
    if expiry <= start:
        raise HTTPException(422, "The expiry date must be after the start date")
    modules = _clean_modules(body.modules)
    try:
        org, admin = create_company(db, OrganizationAdminCreate(code=body.code, name=body.name, legal_name=body.legal_name, country=body.country, admin_email=body.admin_email, admin_username=body.admin_username,
                                                                admin_password=body.admin_password, admin_first_name=body.admin_first_name, admin_last_name=body.admin_last_name), user, commit=False)
        org.license_start, org.license_expiry, org.renewal_cycle = start, expiry, body.renewal_cycle
        org.max_users, org.max_sessions, org.modules, org.license_notes = body.max_users, body.max_sessions, modules, body.license_notes
        if body.maintenance_plan:
            org.maintenance_plan, org.maintenance_expiry = body.maintenance_plan, lm.add_months(start, lm.PLANS[body.maintenance_plan])
        org.contact = {k: v for k, v in {"person": body.contact_person, "email": body.contact_email, "phone": body.contact_phone, "address": body.address}.items() if v}
        db.flush()
        record_audit(db, "LICENSE_ISSUED", "LICENSE", user, org.id, "Organization", str(org.id), {"license": org.license_number, "start": str(start), "expiry": str(expiry), "cycle": body.renewal_cycle, "max_users": body.max_users, "modules": modules})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(409, "The company could not be created (the code, user name or email may already exist)")
    return {"company": _detail(db, org), "admin_username": admin.username}


class PatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    contact_person: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=400)
    max_users: int | None = Field(default=None, ge=1, le=100000)
    max_sessions: int | None = Field(default=None, ge=1, le=100000)
    unlimited_users: bool | None = None
    unlimited_sessions: bool | None = None
    modules: list[str] | None = None
    all_modules: bool | None = None
    maintenance_plan: str | None = Field(default=None, pattern="^(AMC|MMC|NONE)$")
    license_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("contact_email")
    @classmethod
    def _mail(cls, v):
        if v and not EMAIL.match(v):
            raise ValueError("Email ID is not valid")
        return v


@router.patch("/companies/{org_id}")
def edit_company(org_id: int, body: PatchIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Only the permitted fields change (name, contact, limits, modules, notes). The license number, status and dates are changed by their own actions."""
    org = _org(db, org_id)
    data = body.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}

    def put(field: str, new: Any, old: Any):
        if new != old:
            changes[field] = {"from": old, "to": new}

    if "name" in data:
        put("name", data["name"], org.name); org.name = data["name"]
    if "legal_name" in data:
        put("legal_name", data["legal_name"], org.legal_name); org.legal_name = data["legal_name"]
    if any(k in data for k in ("contact_person", "contact_email", "contact_phone", "address")):
        old = dict(org.contact or {})
        new = {**old, **{k[8:] if k.startswith("contact_") else k: v for k, v in data.items() if k in ("contact_person", "contact_email", "contact_phone", "address")}}
        new = {k: v for k, v in new.items() if v}
        put("contact", new, old); org.contact = new
    if data.get("unlimited_users"):
        put("max_users", None, org.max_users); org.max_users = None
    elif data.get("max_users") is not None:
        if lm.enabled_user_count(db, org.id) > data["max_users"]:
            raise HTTPException(409, f"{lm.enabled_user_count(db, org.id)} user(s) are enabled; disable some before lowering the limit to {data['max_users']}")
        put("max_users", data["max_users"], org.max_users); org.max_users = data["max_users"]
    if data.get("unlimited_sessions"):
        put("max_sessions", None, org.max_sessions); org.max_sessions = None
    elif data.get("max_sessions") is not None:
        put("max_sessions", data["max_sessions"], org.max_sessions); org.max_sessions = data["max_sessions"]
    if data.get("all_modules"):
        put("modules", None, org.modules); org.modules = None
    elif data.get("modules") is not None:
        mods = _clean_modules(data["modules"])
        put("modules", mods, org.modules); org.modules = mods
    if "license_notes" in data:
        put("license_notes", data["license_notes"], org.license_notes); org.license_notes = data["license_notes"]
    if "maintenance_plan" in data:
        plan = None if data["maintenance_plan"] in (None, "NONE") else data["maintenance_plan"]
        put("maintenance_plan", plan, org.maintenance_plan)
        org.maintenance_plan = plan
        if plan is None:
            org.maintenance_expiry = None
        elif org.maintenance_expiry is None:
            org.maintenance_expiry = lm.add_months(lm.today_utc(), lm.PLANS[plan])
    if changes:
        record_audit(db, "LICENSE_EDITED", "LICENSE", user, org.id, "Organization", str(org.id), changes)
        db.commit()
    return _detail(db, org)


# ---------------------------------------------------------------- lifecycle
class RenewIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)          # one per click of the Renew button: a repeat never extends twice
    kind: str = Field(default="LICENSE", pattern="^(LICENSE|MAINTENANCE)$")
    cycle: str = Field(default="YEARLY", pattern="^(MONTHLY|YEARLY)$")
    plan: str | None = Field(default=None, pattern="^(AMC|MMC)$")      # maintenance contract: AMC = annual, MMC = monthly
    reference: str | None = Field(default=None, max_length=120)       # invoice / receipt number
    amount: float | None = Field(default=None, ge=0, le=100000000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_method: str | None = Field(default=None, max_length=20)
    payment_status: str = Field(default="PENDING", pattern="^(PENDING|RECEIVED|WAIVED)$")    # what was recorded; the system does not check any bank


@router.post("/companies/{org_id}/renew")
def renew_license(org_id: int, body: RenewIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    org = _org(db, org_id)
    rec, fresh = lm.renew(db, org, body.cycle, body.request_id, body.reference, user, kind=body.kind, amount=body.amount, currency=(body.currency or "").upper() or None, payment_method=body.payment_method,
                          payment_status=body.payment_status, plan=body.plan)
    if fresh:                                      # a renewal extends the dates only; a suspension is lifted by Reactivate
        record_audit(db, "LICENSE_RENEWED" if body.kind == "LICENSE" else "MAINTENANCE_RENEWED", "LICENSE", user, org.id, "Organization", str(org.id),
                     {"kind": body.kind, "cycle": rec.cycle, "from": str(rec.previous_expiry), "to": str(rec.new_expiry), "reference": body.reference, "amount": body.amount, "payment_status": body.payment_status})
    db.commit()
    return {"renewed": fresh, "company": _detail(db, org)}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _transition(db: Session, user: User, org: Organization, allowed_from: tuple[str, ...], to: str, action: str, reason: str | None):
    if (org.license_number or "").upper() == lm.OWNER_LICENSE:
        raise HTTPException(409, "The owner company's license cannot be suspended or cancelled")
    if org.status not in allowed_from:
        raise HTTPException(409, f"A {org.status.lower()} license cannot be changed to {to.lower()} this way")
    before, org.status = org.status, to
    record_audit(db, action, "LICENSE", user, org.id, "Organization", str(org.id), {"from": before, "to": to, "reason": reason})
    db.commit()
    return _detail(db, org)


@router.post("/companies/{org_id}/suspend")
def suspend(org_id: int, body: ReasonIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    return _transition(db, user, _org(db, org_id), ("ACTIVE",), "SUSPENDED", "LICENSE_SUSPENDED", body.reason)


@router.post("/companies/{org_id}/reactivate")
def reactivate(org_id: int, body: ReasonIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    return _transition(db, user, _org(db, org_id), ("SUSPENDED", "CANCELLED"), "ACTIVE", "LICENSE_REACTIVATED", body.reason)


@router.post("/companies/{org_id}/cancel")
def cancel(org_id: int, body: ReasonIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    return _transition(db, user, _org(db, org_id), ("ACTIVE", "SUSPENDED"), "CANCELLED", "LICENSE_CANCELLED", body.reason)


# ---------------------------------------------------------------- details, users, usage, audit
def _user_row(u: User) -> dict:
    live = bool(config.SINGLE_SESSION and u.session_id and u.session_seen_at and (datetime.now(timezone.utc) - u.session_seen_at).total_seconds() < config.SESSION_ACTIVE_MINUTES * 60)
    return {"id": u.id, "username": u.username, "name": (u.first_name + " " + u.last_name).strip(), "status": u.status, "roles": [r.name for r in u.roles], "last_login": u.last_login_at,
            "live_session": live if config.SINGLE_SESSION else None, "last_activity": u.session_seen_at if config.SINGLE_SESSION else None, "locked": bool(u.locked_until and u.locked_until > datetime.now(timezone.utc))}


def _renewal_row(r: LicenseRenewal, names: dict) -> dict:
    return {"id": r.id, "kind": r.kind, "cycle": r.cycle, "previous_expiry": r.previous_expiry, "new_expiry": r.new_expiry, "reference": r.reference, "by": names.get(r.performed_by), "at": r.performed_at,
            "amount": str(r.amount) if r.amount is not None else None, "currency": r.currency, "payment_method": r.payment_method, "payment_status": r.payment_status, "payment_updated_at": r.payment_updated_at}


def _detail(db: Session, org: Organization) -> dict:
    row = _rows(db, [org])[0]
    users = list(db.scalars(select(User).options(selectinload(User.roles)).where(User.organization_id == org.id, User.is_super_admin.is_(False)).order_by(User.id)))
    renewals = list(db.scalars(select(LicenseRenewal).where(LicenseRenewal.organization_id == org.id).order_by(LicenseRenewal.id.desc()).limit(50)))
    notes = list(db.scalars(select(LicenseNotification).where(LicenseNotification.organization_id == org.id).order_by(LicenseNotification.id.desc()).limit(20)))
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({r.performed_by for r in renewals if r.performed_by})))} if renewals else {}
    return {**row, "legal_name": org.legal_name, "country": org.country, "contact": org.contact or {}, "license_notes": org.license_notes, "created_at": org.created_at,
            "users_list": [_user_row(u) for u in users], "enabled_users": row["users"], "total_users": len(users), "session_tracking": config.SINGLE_SESSION,
            "renewals": [_renewal_row(r, names) for r in renewals],
            "notifications": [{"id": n.id, "kind": n.kind, "message": n.message, "status": n.status, "channel_status": n.channel_status, "attempts": n.attempts, "created_at": n.created_at} for n in notes],
            "available_modules": list(lm.MODULES)}


@router.get("/companies/{org_id}")
def company_detail(org_id: int, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    return _detail(db, _org(db, org_id))


def _audit_rows(db: Session, org_id: int | None, limit: int, modules: tuple[str, ...] | None = None):
    q = select(AuditLog)
    if org_id is not None:
        q = q.where(AuditLog.organization_id == org_id)
    if modules:
        q = q.where(AuditLog.module.in_(modules))
    rows = list(db.scalars(q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)))
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))} if rows else {}
    return [{"id": r.id, "when": r.created_at, "user": names.get(r.user_id), "action": r.action, "module": r.module, "entity": r.entity_type, "entity_id": r.entity_id, "company_id": r.organization_id,
             "detail": {k: v for k, v in (r.new_value or {}).items() if k not in ("user_agent",)}} for r in rows]


@router.get("/audit")
def license_audit(company_id: int | None = None, limit: int = Query(default=200, ge=1, le=1000), user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """The license trail: issuing, editing, renewing, suspending, reactivating, cancelling, and refused attempts. Read-only; there is no way to change or delete it here."""
    return _audit_rows(db, company_id, limit, ("LICENSE",))


@router.get("/usage")
def usage(company_id: int | None = None, days: int = Query(default=30, ge=1, le=365), category: str = Query(default="all", pattern="^(all|login|failed|activity)$"), limit: int = Query(default=200, ge=1, le=1000),
          user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Usage from the audit trail the ERP already writes: sign-ins, failed sign-ins, and what users did per module. Live sessions are shown only where session tracking is on."""
    if company_id is not None:
        _org(db, company_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(AuditLog).where(AuditLog.created_at >= since)
    if company_id is not None:
        q = q.where(AuditLog.organization_id == company_id)
    else:
        q = q.where(AuditLog.organization_id.is_not(None))
    logs = list(db.scalars(q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(5000)))
    logins = [l for l in logs if l.module == "AUTHENTICATION" and l.action == "LOGIN"]
    failed = [l for l in logs if l.action in ("LOGIN_FAILED", "ACCOUNT_LOCKED", "LOGIN_BLOCKED_LOCKED", "LOGIN_BLOCKED_LICENSE")]
    work = [l for l in logs if l.module not in ("AUTHENTICATION", "LICENSE")]
    shown = {"all": logs, "login": logins, "failed": failed, "activity": work}[category][:limit]
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({l.user_id for l in shown if l.user_id})))} if shown else {}
    return {"days": days, "session_tracking": config.SINGLE_SESSION,
            "totals": {"logins": len(logins), "failed_attempts": len(failed), "actions": len(work), "distinct_users": len({l.user_id for l in logins if l.user_id}), "distinct_active_users": len({l.user_id for l in logs if l.user_id and l.module != "LICENSE"})},
            "by_module": [{"module": m, "actions": n} for m, n in Counter(l.module for l in work).most_common()],
            "events": [{"when": l.created_at, "user": names.get(l.user_id), "action": l.action, "module": l.module, "entity": l.entity_type, "company_id": l.organization_id} for l in shown]}


# ---------------------------------------------------------------- reminders
@router.get("/notifications")
def notifications(status_filter: str | None = Query(default=None, alias="status", pattern="^(OPEN|ACKNOWLEDGED)$"), limit: int = Query(default=100, ge=1, le=500), user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    q = select(LicenseNotification, Organization).join(Organization, Organization.id == LicenseNotification.organization_id)
    if status_filter:
        q = q.where(LicenseNotification.status == status_filter)
    out = [{"id": n.id, "company_id": o.id, "company": o.name, "license": o.license_number, "kind": n.kind, "expiry_date": n.expiry_date, "message": n.message, "status": n.status, "channel_status": n.channel_status,
            "attempts": n.attempts, "created_at": n.created_at} for n, o in db.execute(q.order_by(LicenseNotification.id.desc()).limit(limit))]
    return out


@router.post("/notifications/run")
def run_notifications(user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Create the reminders that are due now (the same job also runs by itself every few hours and at start-up). Repeating it creates nothing twice."""
    return {"created": lm.generate_notifications(db)}


@router.post("/notifications/{nid}/ack")
def ack_notification(nid: int, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    n = db.get(LicenseNotification, nid)
    if n is None:
        raise HTTPException(404, "Reminder not found")
    n.status = "ACKNOWLEDGED"
    db.commit()
    return {"id": n.id, "status": n.status}


@router.post("/notifications/{nid}/retry")
def retry_notification(nid: int, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Re-attempt the e-mail delivery of a reminder. No e-mail service is configured in this installation, so the attempt is recorded and the reminder stays visible here."""
    n = db.get(LicenseNotification, nid)
    if n is None:
        raise HTTPException(404, "Reminder not found")
    n.attempts += 1
    n.last_attempt_at = datetime.now(timezone.utc)
    n.channel_status = "EMAIL_NOT_CONFIGURED"
    db.commit()
    return {"id": n.id, "channel_status": n.channel_status, "attempts": n.attempts}


# ---------------------------------------------------------------- live, ranking and storage (the provider's view of every company)
def _fmt_user(u: User, org: Organization | None, usage_row: UserUsage | None) -> dict:
    return {"user_id": u.id, "username": u.username, "name": (u.first_name + " " + u.last_name).strip(), "company": org.name if org else None, "license": org.license_number if org else None, "company_id": org.id if org else None,
            "roles": [r.name for r in u.roles], "last_seen": usage_row.last_seen_at if usage_row else None, "minutes_today": (usage_row.active_seconds // 60) if usage_row else 0, "last_login": u.last_login_at}


@router.get("/live")
def live_users(user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Who is online right now (active in the last 5 minutes) across every company, with the time they have been active today. Based on request activity, so it works without the single-session setting."""
    now = datetime.now(timezone.utc)
    rows = db.execute(select(UserUsage, User, Organization).join(User, User.id == UserUsage.user_id).outerjoin(Organization, Organization.id == UserUsage.organization_id)
                      .where(UserUsage.day == now.date(), UserUsage.last_seen_at >= now - timedelta(minutes=lm.ONLINE_MINUTES)).order_by(UserUsage.last_seen_at.desc())).all()
    items = []
    for ur, u, o in rows:
        items.append(_fmt_user(u, o, ur))
    week = now - timedelta(days=7)
    return {"online_minutes_window": lm.ONLINE_MINUTES, "online_now": len(items), "items": items,
            "signins_7d": db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.module == "AUTHENTICATION", AuditLog.action == "LOGIN", AuditLog.created_at >= week)) or 0,
            "failed_7d": db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(("LOGIN_FAILED", "ACCOUNT_LOCKED")), AuditLog.created_at >= week)) or 0}


@router.get("/ranking")
def ranking(days: int = Query(default=30, ge=1, le=365), user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Top companies and users by time online, the companies with the most users, and the companies / users that were never or not recently active."""
    since = date.today() - timedelta(days=days - 1)
    orgs = {o.id: o for o in db.scalars(select(Organization).where(Organization.code != "RTS-PLATFORM"))}
    by_org = Counter()
    by_user = Counter()
    days_org: dict[int, set] = {}
    for oid, uid, secs, day in db.execute(select(UserUsage.organization_id, UserUsage.user_id, UserUsage.active_seconds, UserUsage.day).where(UserUsage.day >= since)):
        if oid in orgs:
            by_org[oid] += secs
            by_user[uid] += secs
            days_org.setdefault(oid, set()).add(day)
    enabled = Counter(oid for oid in db.scalars(select(User.organization_id).where(User.status == "ACTIVE", User.is_super_admin.is_(False), User.organization_id.is_not(None))))
    users = {u.id: u for u in db.scalars(select(User).options(selectinload(User.roles)).where(User.id.in_(list(by_user)[:500])))} if by_user else {}
    top_companies = [{"company_id": oid, "company": orgs[oid].name, "license": orgs[oid].license_number, "minutes": secs // 60, "active_days": len(days_org.get(oid, ())), "enabled_users": enabled.get(oid, 0)} for oid, secs in by_org.most_common(10)]
    top_users = [{"user_id": uid, "username": users[uid].username, "company": orgs[users[uid].organization_id].name if users[uid].organization_id in orgs else None, "license": orgs[users[uid].organization_id].license_number if users[uid].organization_id in orgs else None,
                  "minutes": secs // 60} for uid, secs in by_user.most_common(10) if uid in users]
    biggest = [{"company_id": oid, "company": orgs[oid].name, "license": orgs[oid].license_number, "enabled_users": n, "max_users": orgs[oid].max_users} for oid, n in enabled.most_common(10) if oid in orgs]
    now = datetime.now(timezone.utc)
    last_seen = {oid: ls for oid, ls in db.execute(select(UserUsage.organization_id, func.max(UserUsage.last_seen_at)).group_by(UserUsage.organization_id))}
    idle = [{"company_id": o.id, "company": o.name, "license": o.license_number, "status": lm.effective_status(o), "last_active": last_seen.get(o.id)} for o in orgs.values() if o.id not in by_org]
    all_users = list(db.scalars(select(User).where(User.is_super_admin.is_(False), User.organization_id.is_not(None))))
    active_u = len([u for u in all_users if u.status == "ACTIVE"])
    recent = len({uid for uid in by_user})
    return {"days": days, "top_companies_by_online_time": top_companies, "top_users_by_online_time": top_users, "companies_with_most_users": biggest, "companies_not_active_in_period": idle[:50],
            "users": {"total": len(all_users), "enabled": active_u, "disabled": len(all_users) - active_u, "active_in_period": recent, "not_active_in_period": max(0, active_u - recent)}}


_storage_cache: dict = {"at": None, "data": None}


@router.get("/storage")
def storage(refresh: bool = False, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Data stored per company (approximate size of all its records, pictures and documents in the database) and record counts. Cached for five minutes; refresh=true recounts."""
    now = datetime.now(timezone.utc)
    if refresh or _storage_cache["data"] is None or (now - _storage_cache["at"]).total_seconds() > 300:
        _storage_cache["data"], _storage_cache["at"] = lm.storage_by_company(db), now
    data = _storage_cache["data"]
    orgs = [o for o in db.scalars(select(Organization).order_by(Organization.id)) if o.code != "RTS-PLATFORM"]
    items = []
    for o in orgs:
        s = data.get(o.id, {"bytes": 0, "records": 0, "tables": {}})
        t = s["tables"]
        items.append({"company_id": o.id, "company": o.name, "license": o.license_number, "bytes": s["bytes"], "records": s["records"], "jobs": t.get("shipments", 0), "documents": t.get("documents", 0), "customers": t.get("customers", 0), "bookings": t.get("bookings", 0), "quotations": t.get("quotations", 0)})
    items.sort(key=lambda x: -x["bytes"])
    return {"as_of": _storage_cache["at"], "total_bytes": sum(i["bytes"] for i in items), "items": items, "note": "Approximate: the size of the rows stored for each company in the database."}


# ---------------------------------------------------------------- renewals and payments across companies
@router.get("/renewals")
def all_renewals(payment_status: str | None = Query(default=None, pattern="^(PENDING|RECEIVED|WAIVED)$"), kind: str | None = Query(default=None, pattern="^(LICENSE|MAINTENANCE)$"), limit: int = Query(default=200, ge=1, le=1000),
                 user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    q = select(LicenseRenewal, Organization).join(Organization, Organization.id == LicenseRenewal.organization_id)
    if payment_status:
        q = q.where(LicenseRenewal.payment_status == payment_status)
    if kind:
        q = q.where(LicenseRenewal.kind == kind)
    rows = db.execute(q.order_by(LicenseRenewal.id.desc()).limit(limit)).all()
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({r.performed_by for r, _ in rows if r.performed_by})))} if rows else {}
    pending = db.execute(select(func.count(), func.coalesce(func.sum(LicenseRenewal.amount), 0)).where(LicenseRenewal.payment_status == "PENDING")).one()
    return {"pending_count": pending[0], "pending_amount": str(pending[1]), "items": [{**_renewal_row(r, names), "company_id": o.id, "company": o.name, "license": o.license_number} for r, o in rows]}


class PaymentIn(BaseModel):
    payment_status: str = Field(pattern="^(PENDING|RECEIVED|WAIVED)$")
    payment_method: str | None = Field(default=None, max_length=20)
    reference: str | None = Field(default=None, max_length=120)
    amount: float | None = Field(default=None, ge=0, le=100000000)


@router.post("/renewals/{rid}/payment")
def update_payment(rid: int, body: PaymentIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Record the payment of a renewal (what you were told or saw; the system does not check any bank). Audited; the renewal dates never change here."""
    r = db.get(LicenseRenewal, rid)
    if r is None:
        raise HTTPException(404, "Renewal not found")
    if body.payment_method is not None and body.payment_method not in lm.PAY_METHODS:
        raise HTTPException(422, "Payment method must be one of: " + ", ".join(lm.PAY_METHODS))
    before = {"payment_status": r.payment_status, "payment_method": r.payment_method, "reference": r.reference, "amount": str(r.amount) if r.amount is not None else None}
    r.payment_status, r.payment_updated_at = body.payment_status, datetime.now(timezone.utc)
    if body.payment_method is not None:
        r.payment_method = body.payment_method
    if body.reference is not None:
        r.reference = body.reference or None
    if body.amount is not None:
        r.amount = body.amount
    record_audit(db, "LICENSE_PAYMENT_RECORDED", "LICENSE", user, r.organization_id, "LicenseRenewal", str(r.id), {"from": before, "to": {"payment_status": r.payment_status, "payment_method": r.payment_method, "reference": r.reference, "amount": body.amount}})
    db.commit()
    return _renewal_row(r, {})


# ---------------------------------------------------------------- users, permissions and passwords of every company
class UserAdminIn(BaseModel):
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    role: str | None = None
    new_password: str | None = Field(default=None, min_length=1, max_length=256)      # the provider sets it; any length
    unlock: bool | None = None


def _target(db: Session, org_id: int, uid: int) -> User:
    org = _org(db, org_id)
    u = db.scalar(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == uid, User.organization_id == org.id, User.is_super_admin.is_(False)))
    if u is None:
        raise HTTPException(404, "User not found in this company")
    return u


@router.get("/companies/{org_id}/users/{uid}")
def user_detail(org_id: int, uid: int, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """One user with the roles and the exact permissions they give. No password or hash is ever returned."""
    u = _target(db, org_id, uid)
    perms = sorted({p.code for r in u.roles for p in r.permissions})
    return {**_user_row(u), "email": u.email, "phone": u.phone, "permissions": perms, "available_roles": list(lm_roles())}


def lm_roles():
    from .roles import ROLE_LABELS

    return ROLE_LABELS.items()


@router.patch("/companies/{org_id}/users/{uid}")
def manage_user(org_id: int, uid: int, body: UserAdminIn, user: User = Depends(lm.require_license_admin), db: Session = Depends(get_db)):
    """Provider-side user management: enable / disable (the licensed user limit applies), change the role, unlock, and set a new password. Each is audited; the password itself is never logged."""
    from .roles import ROLE_PERMISSIONS, ensure_role
    from .security import hash_password

    u = _target(db, org_id, uid)
    if u.id == user.id and (body.status == "INACTIVE" or body.role):
        raise HTTPException(409, "You cannot disable yourself or change your own role here")
    if any(r.name == "LICENSE_SUPER_ADMIN" for r in u.roles) and u.id != user.id and (body.status == "INACTIVE" or body.role):
        raise HTTPException(409, "The License Management administrator cannot be disabled or re-roled from here")
    changes: dict[str, Any] = {}
    if body.status and body.status != u.status:
        if body.status == "ACTIVE":
            lm.assert_user_capacity(db, org_id)
        changes["status"] = {"from": u.status, "to": body.status}
        u.status = body.status
        if body.status == "INACTIVE":
            u.session_id = u.session_ip = u.session_started_at = u.session_seen_at = None
    if body.role:
        if body.role not in ROLE_PERMISSIONS:
            raise HTTPException(422, "Unknown role")
        changes["role"] = {"from": [r.name for r in u.roles if r.name != "LICENSE_SUPER_ADMIN"], "to": body.role}
        u.roles = [ensure_role(db, org_id, body.role)] + [r for r in u.roles if r.name == "LICENSE_SUPER_ADMIN"]
    if body.unlock:
        u.failed_login_count, u.locked_until = 0, None
        changes["unlocked"] = True
    if body.new_password:
        u.password_hash = hash_password(body.new_password)
        u.failed_login_count, u.locked_until = 0, None
        u.session_id = u.session_ip = u.session_started_at = u.session_seen_at = None
        changes["password"] = "reset by License Management"
    if changes:
        record_audit(db, "LICENSE_USER_MANAGED", "LICENSE", user, org_id, "User", str(u.id), {"username": u.username, **changes})
        db.commit()
    return {**_user_row(u), "changes": sorted(changes)}
