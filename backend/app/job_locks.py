"""One editor per job. A lock is a lease: every open tab of the editor renews it with a heartbeat, and a lock nobody renews expires,
so a crashed browser never blocks a job for long. The server (middleware) refuses job writes from anyone but the holder."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_current_user, has_permission
from .db import SessionLocal, get_db
from .models import JobLock, JobLockEvent, JobLockRequest, Shipment, User
from .security import decode_token

LEASE_SECONDS = 90          # a tab not heard from for this long no longer holds the lock
HEARTBEAT_SECONDS = 25      # how often the browser renews it
router = APIRouter(prefix="/api/job-locks", tags=["job-locks"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def display_name(u: User) -> str:
    return (" ".join(x for x in [u.first_name, u.last_name] if x) or u.username or u.email or "User").strip()


def log(db: Session, org: int, job: str, user: User | None, event: str, detail: str = "", notify: int | None = None) -> None:
    db.add(JobLockEvent(organization_id=org, job_number=job, user_id=user.id if user else None, user_name=display_name(user) if user else "", event=event, detail=detail, notify_user_id=notify, seen=notify is None))


def live_tabs(lock: JobLock, at: datetime) -> dict:
    cut = at - timedelta(seconds=LEASE_SECONDS)
    out = {}
    for tab, seen in (lock.tabs or {}).items():
        try:
            if datetime.fromisoformat(seen) > cut:
                out[tab] = seen
        except ValueError:
            pass
    return out


def current(db: Session, org: int, job: str) -> JobLock | None:
    """The valid lock on a job; an expired one is removed (and logged) here."""
    lock = db.scalar(select(JobLock).where(JobLock.organization_id == org, JobLock.job_number == job))
    if not lock:
        return None
    tabs = live_tabs(lock, now())
    if not tabs:
        log(db, org, job, None, "EXPIRED", f"The lock of {lock.user_name} expired (no activity)", notify=lock.user_id)
        db.delete(lock)
        db.flush()
        return None
    lock.tabs = tabs
    return lock


def holder_info(lock: JobLock) -> dict:
    return {"user_id": lock.user_id, "user_name": lock.user_name, "job_number": lock.job_number, "job_type": lock.job_type,
            "started_at": lock.started_at.isoformat() if lock.started_at else None, "last_activity": lock.last_activity.isoformat() if lock.last_activity else None}


def renew(lock: JobLock, tab: str) -> None:
    at = now()
    tabs = dict(lock.tabs or {})
    tabs[tab] = at.isoformat()
    lock.tabs = tabs
    lock.last_activity = at
    lock.expires_at = at + timedelta(seconds=LEASE_SECONDS)


def job_type_of(db: Session, org: int, job: str, hint: str = "") -> str:
    s = db.scalar(select(Shipment).where(Shipment.organization_id == org, Shipment.job_number == job))
    if s is None:
        return hint or "Other"
    if s.job_type == "Customs":
        return "CHA"
    if s.job_type == "Transport":
        return "Transport"
    return "Freight"


class TabIn(BaseModel):
    tab_id: str = Field(min_length=4, max_length=60)
    job_type: str = ""


class RespondIn(BaseModel):
    approve: bool


class TakeoverIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    tab_id: str = Field(min_length=4, max_length=60)


def acquire_lock(db: Session, user: User, job: str, tab: str, hint: str = "") -> tuple[bool, JobLock]:
    org = user.organization_id
    lock = current(db, org, job)
    if lock and lock.user_id != user.id:
        return False, lock
    if not lock:
        at = now()
        lock = JobLock(organization_id=org, job_number=job, job_type=job_type_of(db, org, job, hint), user_id=user.id, user_name=display_name(user),
                       started_at=at, last_activity=at, expires_at=at + timedelta(seconds=LEASE_SECONDS), tabs={})
        db.add(lock)
        log(db, org, job, user, "ACQUIRED", f"{display_name(user)} started editing")
    renew(lock, tab)
    return True, lock


def _notices(db: Session, org: int, user: User, job: str) -> list[dict]:
    rows = list(db.scalars(select(JobLockEvent).where(JobLockEvent.organization_id == org, JobLockEvent.notify_user_id == user.id, JobLockEvent.seen.is_(False), JobLockEvent.job_number == job)))
    for e in rows:
        e.seen = True
    return [{"id": e.id, "event": e.event, "detail": e.detail} for e in rows]


@router.post("/requests/{request_id}/respond")
def respond(request_id: int, body: RespondIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(JobLockRequest, request_id)
    if not r or r.organization_id != user.organization_id or r.status != "PENDING":
        raise HTTPException(404, "Request not found")
    if r.holder_id != user.id and not has_permission(user, "job.lock_override"):
        raise HTTPException(403, "Only the current editor (or an administrator) can answer this request")
    org, job = r.organization_id, r.job_number
    r.status = "APPROVED" if body.approve else "DENIED"
    r.resolved_at = now()
    if body.approve:
        lock = current(db, org, job)
        requester = db.get(User, r.requester_id)
        if lock and requester:
            lock.user_id, lock.user_name = requester.id, display_name(requester)
            lock.started_at = lock.last_activity = now()
            lock.expires_at = now() + timedelta(seconds=LEASE_SECONDS)
            lock.tabs = {f"handover-{r.id}": now().isoformat()}     # the requester's own tab renews it when it reopens the job for editing
        log(db, org, job, user, "TRANSFERRED", f"{display_name(user)} handed editing to {r.requester_name}", notify=r.requester_id)
    else:
        log(db, org, job, user, "DENIED", f"{display_name(user)} declined {r.requester_name}'s request", notify=r.requester_id)
    db.commit()
    return {"status": r.status}


@router.post("/{job_number:path}/acquire")
def acquire(job_number: str, body: TabIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ok, lock = acquire_lock(db, user, job_number, body.tab_id, body.job_type)
    out = {"acquired": ok, "holder": holder_info(lock), "heartbeat_seconds": HEARTBEAT_SECONDS, "lease_seconds": LEASE_SECONDS, "can_override": has_permission(user, "job.lock_override")}
    db.commit()
    return out


@router.post("/{job_number:path}/heartbeat")
def heartbeat(job_number: str, body: TabIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = user.organization_id
    lock = current(db, org, job_number)
    notes = _notices(db, org, user, job_number)
    if not lock or lock.user_id != user.id:
        out = {"valid": False, "holder": holder_info(lock) if lock else None, "notices": notes, "requests": []}
        db.commit()
        return out
    renew(lock, body.tab_id)
    reqs = [{"id": r.id, "requester": r.requester_name, "at": r.created_at.isoformat()} for r in db.scalars(select(JobLockRequest).where(
        JobLockRequest.organization_id == org, JobLockRequest.job_number == job_number, JobLockRequest.holder_id == user.id, JobLockRequest.status == "PENDING"))]
    out = {"valid": True, "expires_at": lock.expires_at.isoformat(), "notices": notes, "requests": reqs}
    db.commit()
    return out


@router.post("/{job_number:path}/release")
def release(job_number: str, body: TabIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = user.organization_id
    lock = current(db, org, job_number)
    if lock and lock.user_id == user.id:           # only the holder releases; another open tab of the same user keeps the lock alive
        tabs = dict(lock.tabs or {})
        tabs.pop(body.tab_id, None)
        tabs = {k: v for k, v in tabs.items() if not k.startswith("handover-")} if body.tab_id else tabs      # the placeholder of a hand-over ends with the receiver's own tab
        if tabs:
            lock.tabs = tabs
        else:
            db.delete(lock)
            log(db, org, job_number, user, "RELEASED", f"{display_name(user)} stopped editing")
    db.commit()
    return {"released": True}


@router.get("/{job_number:path}/events")
def events(job_number: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(select(JobLockEvent).where(JobLockEvent.organization_id == user.organization_id, JobLockEvent.job_number == job_number).order_by(JobLockEvent.id.desc()).limit(100))
    return [{"event": e.event, "user": e.user_name, "detail": e.detail, "at": e.created_at.isoformat()} for e in rows]


@router.post("/{job_number:path}/request")
def request_access(job_number: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = user.organization_id
    lock = current(db, org, job_number)
    if not lock:
        db.commit()
        return {"status": "FREE"}
    if lock.user_id == user.id:
        raise HTTPException(400, "You are already the editor of this job")
    r = db.scalar(select(JobLockRequest).where(JobLockRequest.organization_id == org, JobLockRequest.job_number == job_number, JobLockRequest.requester_id == user.id, JobLockRequest.status == "PENDING"))
    if not r:
        r = JobLockRequest(organization_id=org, job_number=job_number, requester_id=user.id, requester_name=display_name(user), holder_id=lock.user_id)
        db.add(r)
        log(db, org, job_number, user, "REQUESTED", f"{display_name(user)} asked {lock.user_name} for editing access")
        db.flush()
    out = {"status": "PENDING", "request_id": r.id}
    db.commit()
    return out


@router.get("/{job_number:path}/status")
def status_of(job_number: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = user.organization_id
    lock = current(db, org, job_number)
    mine = db.scalar(select(JobLockRequest).where(JobLockRequest.organization_id == org, JobLockRequest.job_number == job_number, JobLockRequest.requester_id == user.id).order_by(JobLockRequest.id.desc()).limit(1))
    out = {"locked": bool(lock), "mine": bool(lock and lock.user_id == user.id), "holder": holder_info(lock) if lock else None, "my_request": mine.status if mine else None, "can_override": has_permission(user, "job.lock_override")}
    db.commit()
    return out


@router.post("/{job_number:path}/takeover")
def takeover(job_number: str, body: TakeoverIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not has_permission(user, "job.lock_override"):
        raise HTTPException(403, "You do not have permission to take over a job from another user")
    org = user.organization_id
    lock = current(db, org, job_number)
    if lock and lock.user_id != user.id:
        log(db, org, job_number, user, "TAKEOVER", f"{display_name(user)} took editing from {lock.user_name}: {body.reason}", notify=lock.user_id)
        lock.user_id, lock.user_name = user.id, display_name(user)
        lock.started_at = lock.last_activity = now()
        lock.tabs = {}
    ok, lock = acquire_lock(db, user, job_number, body.tab_id)
    out = {"acquired": ok, "holder": holder_info(lock)}
    db.commit()
    return out


# ---------------- server-side enforcement ----------------
_SUFFIX = r"(?:/(?:status|move-to-accounts|reopen-from-accounts|details/[^/]+|hbl|bl/stage|send-document|do-issue|milestones/[^/]+|stage|closure/validate|close|reopen|charges))?"
_PATTERNS = [
    re.compile(r"^/api/cha/jobs/(?P<job>.+?)" + _SUFFIX + r"$"),
    re.compile(r"^/api/cha/files/(?P<job>.+)$"),
    re.compile(r"^/api/jobs/by-number/(?P<job>.+?)/charges$"),
    re.compile(r"^/api/jobs/(?P<job>.+?)" + _SUFFIX + r"$"),
]
_WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def job_of_path(method: str, path: str) -> str | None:
    if method not in _WRITES:
        return None
    for p in _PATTERNS:
        m = p.match(path)
        if m:
            job = m.group("job").strip("/")
            if not job or job in ("by-number", "validate") or job.endswith(("/cha-jobs", "/duplicate")) or "/" in job and job.split("/")[0] in ("by-number",):
                return None
            return job
    return None


def check_write(method: str, path: str, auth: str | None) -> dict | None:
    """The 423 body when the job is being edited by someone else; None when the write may go ahead."""
    job = job_of_path(method, path)
    if not job or not auth or not auth.lower().startswith("bearer "):
        return None
    try:
        uid = int(decode_token(auth.split(" ", 1)[1])["sub"])
    except Exception:
        return None
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        if not u or not u.organization_id:
            return None
        lock = current(db, u.organization_id, job)
        db.commit()
        if lock and lock.user_id != uid:
            return {"detail": {"code": "JOB_LOCKED", "message": f"This job is currently being processed by {lock.user_name}.", **holder_info(lock)}}
        return None
    finally:
        db.close()
