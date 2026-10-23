"""License Management core: who may manage licenses, license status rules, renewal date arithmetic, expiry reminders and the access policy.

Authority: only a user of the OWNER company (license SWF00001 by default) who was explicitly given the permission `license.manage`. The permission is never part of any
template role (not even Admin) and cannot be granted through the user screens; being the platform super admin is not enough either. Every check is made on the server."""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_current_user
from .auth_api import record_audit
from .db import get_db
from .models import LicenseNotification, LicenseRenewal, Organization, Role, User, UserUsage

OWNER_LICENSE = os.getenv("LICENSE_OWNER", "SWF00001").strip().upper()
PERMISSION = "license.manage"
CYCLES = ("MONTHLY", "YEARLY")
PLANS = {"AMC": 12, "MMC": 1}                      # annual / monthly maintenance contract, in months
PAY_METHODS = ("Cash", "Cheque", "NEFT", "RTGS", "IMPS", "UPI", "Bank Transfer", "Card", "Demand Draft")
PAY_STATUSES = ("PENDING", "RECEIVED", "WAIVED")
ONLINE_MINUTES = 5                                  # a user is "online" when active within this many minutes
STORED_STATUSES = ("ACTIVE", "SUSPENDED", "CANCELLED")
MODULES = ("crm", "quotation", "booking", "jobs", "customs", "transport", "warehouse", "accounts", "reports", "masters", "hr")
EXPIRING_DAYS = 30
REMINDERS = (("D30", 30), ("D7", 7), ("D1", 1), ("D0", 0))


# ---------------------------------------------------------------- authority
def is_license_admin(user: User | None) -> bool:
    if user is None or user.status != "ACTIVE" or user.is_super_admin or user.organization is None:
        return False
    if (user.organization.license_number or "").upper() != OWNER_LICENSE or user.organization.status != "ACTIVE":
        return False
    return any(p.code == PERMISSION for role in user.roles for p in role.permissions)


def require_license_admin(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    if not is_license_admin(user):
        try:
            record_audit(db, "LICENSE_ACCESS_DENIED", "LICENSE", user, user.organization_id, "User", str(user.id), {"portal": getattr(user, "_session_portal", None)})
            db.commit()
        except Exception:        # the refusal must not depend on the audit write
            db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="License Management is restricted")
    return user


# ---------------------------------------------------------------- status and access policy
def add_months(d: date, months: int) -> date:
    """d plus whole months; a day that does not exist in the target month becomes that month's last day (31 Jan + 1 month = 28/29 Feb)."""
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def effective_status(org: Organization, today: date | None = None) -> str:
    """The status shown and enforced: a stored SUSPENDED / CANCELLED wins, otherwise the expiry date decides (no expiry date = not time-limited)."""
    today = today or today_utc()
    if org.status in ("SUSPENDED", "CANCELLED"):
        return org.status
    if org.status != "ACTIVE":
        return org.status
    if org.license_expiry is None:
        return "ACTIVE"
    if org.license_expiry < today:
        return "EXPIRED"
    if (org.license_expiry - today).days <= EXPIRING_DAYS:
        return "EXPIRING"
    return "ACTIVE"


def block_reason(org: Organization | None, today: date | None = None) -> str | None:
    """Access policy: a suspended, cancelled or expired company cannot sign in (its data stays untouched); a renewal / reactivation restores access at once.
    The owner company and the platform (no company) are never blocked, so nobody can lock the administrators out."""
    if org is None or (org.license_number or "").upper() == OWNER_LICENSE or org.code == "RTS-PLATFORM":
        return None
    st = effective_status(org, today)
    if st == "SUSPENDED":
        return "This company's license is suspended. Please contact eSway to reactivate it."
    if st == "CANCELLED":
        return "This company's license has been cancelled. Please contact eSway."
    if st == "EXPIRED":
        return f"This company's license expired on {org.license_expiry:%d-%m-%Y}. Please renew it to continue."
    return None


# ---------------------------------------------------------------- users and limits
def enabled_user_count(db: Session, org_id: int) -> int:
    return len(db.scalars(select(User.id).where(User.organization_id == org_id, User.status == "ACTIVE", User.is_super_admin.is_(False))).all())


def assert_user_capacity(db: Session, org_id: int, extra: int = 1) -> None:
    """Server-side licensed-user limit (NULL = unlimited). Called before an enabled user account is created or re-enabled."""
    org = db.get(Organization, org_id)
    if org is not None and org.max_users is not None and enabled_user_count(db, org_id) + extra > org.max_users:
        raise HTTPException(status_code=409, detail=f"The license allows {org.max_users} user(s) and all of them are in use. Disable a user or ask eSway to raise the limit.")


def live_session_count(db: Session, org_id: int, active_minutes: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=active_minutes)
    return len(db.scalars(select(User.id).where(User.organization_id == org_id, User.session_id.is_not(None), User.session_seen_at >= cutoff)).all())


# ---------------------------------------------------------------- renewal
def renew(db: Session, org: Organization, cycle: str, request_id: str, reference: str | None, actor: User, kind: str = "LICENSE", amount=None, currency: str | None = None,
          payment_method: str | None = None, payment_status: str = "PENDING", plan: str | None = None) -> tuple[LicenseRenewal, bool]:
    """Extend the license by one cycle. The new period starts from the current expiry when it is still in the future, otherwise from today.
    A repeated request_id returns the earlier renewal (second return value False) and extends nothing."""
    if kind not in ("LICENSE", "MAINTENANCE"):
        raise HTTPException(422, "Renewal kind must be LICENSE or MAINTENANCE")
    if kind == "LICENSE" and cycle not in CYCLES:
        raise HTTPException(422, "Renewal cycle must be MONTHLY or YEARLY")
    if payment_method is not None and payment_method not in PAY_METHODS:
        raise HTTPException(422, "Payment method must be one of: " + ", ".join(PAY_METHODS))
    if payment_status not in PAY_STATUSES:
        raise HTTPException(422, "Payment status must be PENDING, RECEIVED or WAIVED")
    done = db.scalar(select(LicenseRenewal).where(LicenseRenewal.organization_id == org.id, LicenseRenewal.request_id == request_id))
    if done is not None:
        return done, False
    if org.status == "CANCELLED":
        raise HTTPException(409, "A cancelled license cannot be renewed. Reactivate it first")
    today = today_utc()
    stamp = datetime.now(timezone.utc) if payment_status != "PENDING" else None
    if kind == "MAINTENANCE":
        plan = (plan or org.maintenance_plan or "").upper()
        if plan not in PLANS:
            raise HTTPException(422, "Choose the maintenance contract: AMC (annual) or MMC (monthly)")
        base = org.maintenance_expiry if org.maintenance_expiry and org.maintenance_expiry >= today else today
        new_expiry = add_months(base, PLANS[plan])
        rec = LicenseRenewal(organization_id=org.id, request_id=request_id, kind="MAINTENANCE", cycle=plan, previous_expiry=org.maintenance_expiry, new_expiry=new_expiry, reference=(reference or None), performed_by=actor.id,
                             amount=amount, currency=currency, payment_method=payment_method, payment_status=payment_status, payment_updated_at=stamp)
        db.add(rec)
        org.maintenance_plan, org.maintenance_expiry = plan, new_expiry
        return rec, True
    base = org.license_expiry if org.license_expiry and org.license_expiry >= today else today
    new_expiry = add_months(base, 1 if cycle == "MONTHLY" else 12)
    rec = LicenseRenewal(organization_id=org.id, request_id=request_id, kind="LICENSE", cycle=cycle, previous_expiry=org.license_expiry, new_expiry=new_expiry, reference=(reference or None), performed_by=actor.id,
                         amount=amount, currency=currency, payment_method=payment_method, payment_status=payment_status, payment_updated_at=stamp)
    db.add(rec)
    org.license_expiry, org.renewal_cycle = new_expiry, cycle
    if org.license_start is None:
        org.license_start = today
    return rec, True


# ---------------------------------------------------------------- reminders
def reminder_for(days_left: int) -> str | None:
    """Which reminder applies today: the tightest threshold already reached (30 / 7 / 1 / 0 days), or OVERDUE after the expiry date."""
    if days_left < 0:
        return "OVERDUE"
    for kind, limit in sorted(REMINDERS, key=lambda x: x[1]):
        if days_left <= limit:
            return kind
    return None


def _message(org: Organization, kind: str, days_left: int) -> str:
    name = f"{org.name} ({org.license_number})"
    if kind == "OVERDUE":
        return f"{name}: the license expired {-days_left} day(s) ago on {org.license_expiry:%d-%m-%Y} and has not been renewed."
    if kind == "D0":
        return f"{name}: the license expires today ({org.license_expiry:%d-%m-%Y})."
    return f"{name}: the license expires in {days_left} day(s) on {org.license_expiry:%d-%m-%Y}."


def generate_notifications(db: Session, today: date | None = None) -> int:
    """Create the reminders that are due. Safe to run any number of times and after a restart: a reminder exists once per (company, reminder, expiry date)."""
    today = today or today_utc()
    created = 0
    for org in db.scalars(select(Organization).where(Organization.status == "ACTIVE", Organization.license_expiry.is_not(None))):
        if (org.license_number or "").upper() == OWNER_LICENSE:
            continue
        days_left = (org.license_expiry - today).days
        kind = reminder_for(days_left)
        if kind is None:
            continue
        if db.scalar(select(LicenseNotification.id).where(LicenseNotification.organization_id == org.id, LicenseNotification.kind == kind, LicenseNotification.expiry_date == org.license_expiry)):
            continue
        db.add(LicenseNotification(organization_id=org.id, kind=kind, expiry_date=org.license_expiry, message=_message(org, kind, days_left), channel_status="IN_APP"))
        created += 1
    db.commit()
    return created


def run_notification_job() -> int:
    """Entry point for the background loop and the command line: its own session, never raises."""
    from .db import SessionLocal

    try:
        with SessionLocal() as db:
            return generate_notifications(db)
    except Exception:
        return 0


def summary_row(org: Organization, users: int, sessions: int | None, today: date | None = None) -> dict:
    st = effective_status(org, today)
    today = today or today_utc()
    return {"id": org.id, "code": org.code, "name": org.name, "license_number": org.license_number, "status": st, "stored_status": org.status, "license_start": org.license_start, "license_expiry": org.license_expiry,
            "days_left": (org.license_expiry - today).days if org.license_expiry else None, "renewal_cycle": org.renewal_cycle, "max_users": org.max_users, "max_sessions": org.max_sessions, "users": users,
            "sessions": sessions, "modules": org.modules, "is_owner": (org.license_number or "").upper() == OWNER_LICENSE, "is_demo": bool(org.is_demo),
            "maintenance_plan": org.maintenance_plan, "maintenance_expiry": org.maintenance_expiry, "maintenance_status": maintenance_status(org, today)}


def maintenance_status(org: Organization, today: date | None = None) -> str | None:
    """Status of the AMC / MMC maintenance contract (None = the company has no contract)."""
    if not org.maintenance_plan:
        return None
    today = today or today_utc()
    if org.maintenance_expiry is None:
        return "ACTIVE"
    if org.maintenance_expiry < today:
        return "EXPIRED"
    return "EXPIRING" if (org.maintenance_expiry - today).days <= EXPIRING_DAYS else "ACTIVE"


# ---------------------------------------------------------------- granting the authority (command line only; there is deliberately no screen or API for it)
def grant_license_admin(db: Session, username: str, owner_license: str | None = None) -> User:
    """Give one user of the owner company the License Management authority: a dedicated role that holds only the `license.manage` permission."""
    from .models import Permission

    owner = (owner_license or OWNER_LICENSE).upper()
    org = db.scalar(select(Organization).where(Organization.license_number == owner))
    if org is None:
        raise ValueError("No company with license " + owner)
    user = db.scalar(select(User).where(User.organization_id == org.id, User.username == username.lower()))
    if user is None:
        raise ValueError(f"No user {username} in {owner}")
    perm = db.scalar(select(Permission).where(Permission.code == PERMISSION))
    if perm is None:
        perm = Permission(code=PERMISSION, name="Manage company licenses", module="license", action="manage")
        db.add(perm)
        db.flush()
    role = db.scalar(select(Role).where(Role.organization_id == org.id, Role.name == "LICENSE_SUPER_ADMIN"))
    if role is None:
        role = Role(name="LICENSE_SUPER_ADMIN", organization_id=org.id, description="License Management (owner company only)", status="ACTIVE")
        db.add(role)
    role.permissions = [perm]
    if role not in user.roles:
        user.roles.append(role)
    record_audit(db, "LICENSE_ADMIN_GRANTED", "LICENSE", None, org.id, "User", str(user.id), {"username": user.username})
    db.commit()
    return user


# ---------------------------------------------------------------- usage: online time per user and day
_last_touch: dict[int, float] = {}


def touch_usage(db: Session, user: User) -> None:
    """Record that this user is active now. Written at most every 30 seconds per user, in its own short session, and never allowed to break a request.
    Time online = the gaps between requests that are 5 minutes or shorter (a longer gap starts a new stretch and counts 30 seconds)."""
    import time

    now_ts = time.monotonic()
    if now_ts - _last_touch.get(user.id, -1e9) < 30:
        return
    _last_touch[user.id] = now_ts
    try:
        from .db import SessionLocal

        now = datetime.now(timezone.utc)
        with SessionLocal() as s:
            row = s.scalar(select(UserUsage).where(UserUsage.user_id == user.id, UserUsage.day == now.date()))
            if row is None:
                s.add(UserUsage(user_id=user.id, organization_id=user.organization_id, day=now.date(), active_seconds=30, requests=1, first_seen_at=now, last_seen_at=now))
            else:
                gap = (now - row.last_seen_at).total_seconds()
                row.active_seconds += int(gap) if gap <= ONLINE_MINUTES * 60 else 30
                row.requests += 1
                row.last_seen_at = now
            s.commit()
    except Exception:
        pass


def storage_by_company(db: Session) -> dict[int, dict]:
    """Approximate data stored per company: the size of every row that belongs to it (all tables that carry organization_id; pictures and documents kept in the database are included) plus the main record counts."""
    from sqlalchemy import text

    tables = [r[0] for r in db.execute(text("select table_name from information_schema.columns where column_name = 'organization_id' and table_schema = current_schema() and table_name not in ('audit_logs','user_usage')")).all()]
    out: dict[int, dict] = {}
    for t in tables:
        for org_id, size, n in db.execute(text(f'select organization_id, coalesce(sum(pg_column_size(x.*)), 0), count(*) from "{t}" x where organization_id is not null group by organization_id')).all():
            row = out.setdefault(org_id, {"bytes": 0, "records": 0, "tables": {}})
            row["bytes"] += int(size)
            row["records"] += int(n)
            row["tables"][t] = int(n)
    return out
