"""Job routing: the flights of an air job and the vessel legs of a sea job (with transshipment), each Planned or Confirmed, with follow-up, history, dashboard alerts and tracking.

Routing is its own record set. The job and its booking are only where it starts from (suggested legs); saving a leg never changes them. Every change is kept in job_routing_history
and the job's activity log. Alerts are computed from the saved legs, so they appear, change and disappear as the legs change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import job_lifecycle as jl
from .access import shipment_clause
from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Booking, Customer, JobRoutingHistory, JobRoutingLeg, Shipment, User

router = APIRouter(tags=["job routing"])

STAGES = ("PLANNED", "CONFIRMED")
TRACKED = ("carrier", "voyage", "pol", "pod", "etd", "eta", "stage", "follow_up", "remarks")
LABEL = {"carrier": "Airline / Vessel", "voyage": "Flight / Voyage", "pol": "POL", "pod": "POD", "etd": "ETD", "eta": "ETA", "stage": "Stage", "follow_up": "Follow-up required", "remarks": "Remarks"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job(db: Session, user: User, job_number: str, lock: bool = False) -> Shipment:
    if user.organization_id is None:
        raise HTTPException(403, "Routing is tenant-scoped")
    q = select(Shipment).where(Shipment.organization_id == user.organization_id, Shipment.job_number == job_number)
    clause = shipment_clause(db, user)
    if clause is not None:
        q = q.where(clause)
    job = db.scalar(q.with_for_update() if lock else q)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _kind(job: Shipment) -> str | None:
    m = (job.mode or "").lower()
    return "AIR" if m == "air" else "SEA" if m in ("sea", "ocean") else None


def _fmt(v: Any) -> str:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%d %b %Y")
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return "" if v is None else str(v)


def _leg(r: JobRoutingLeg) -> dict:
    return {"id": r.id, "seq": r.seq, "kind": r.kind, "carrier": r.carrier, "voyage": r.voyage, "pol": r.pol, "pod": r.pod, "etd": r.etd, "eta": r.eta, "stage": r.stage, "follow_up": r.follow_up,
            "follow_up_status": r.follow_up_status, "follow_up_note": r.follow_up_note, "remarks": r.remarks, "source": r.source, "updated_at": r.updated_at}


def _legs(db: Session, job: Shipment) -> list[JobRoutingLeg]:
    return list(db.scalars(select(JobRoutingLeg).where(JobRoutingLeg.shipment_id == job.id, JobRoutingLeg.is_active.is_(True)).order_by(JobRoutingLeg.seq, JobRoutingLeg.id)))


def _at(d) -> datetime | None:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) if d else None


def suggested(db: Session, job: Shipment) -> list[dict]:
    """What the job and its booking already know about the movement, used as the first leg until the user saves routing. Nothing is written."""
    kind = _kind(job)
    if kind is None:
        return []
    b = db.get(Booking, job.booking_id) if job.booking_id else None
    carrier = voyage = None
    if b:
        d = b.data or {}
        raw = (b.vessel or "").strip()
        if kind == "SEA":
            parts = [x.strip() for x in raw.split("/", 1)] if raw else []
            carrier = (parts[0] if parts else None) or None
            voyage = (parts[1] if len(parts) > 1 else None) or (d.get("voyage") or None)
        else:
            carrier = (b.carrier or d.get("airline") or "").strip() or None
            voyage = raw or d.get("flight_no") or None
    etd, eta = (b.etd if b and b.etd else job.etd), (b.eta if b and b.eta else job.eta)
    pol, pod = (b.pol if b and b.pol else job.pol), (b.pod if b and b.pod else job.pod)
    if not any([carrier, voyage, etd, eta]):
        return []
    return [{"id": None, "seq": 1, "kind": kind, "carrier": carrier, "voyage": voyage, "pol": pol if kind == "SEA" else None, "pod": pod if kind == "SEA" else None, "etd": _at(etd), "eta": _at(eta),
             "stage": "PLANNED", "follow_up": False, "follow_up_status": "OPEN", "follow_up_note": None, "remarks": None, "source": "BOOKING" if b else "JOB"}]


class LegIn(BaseModel):
    carrier: str | None = Field(default=None, max_length=120)
    voyage: str | None = Field(default=None, max_length=40)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    etd: datetime | None = None
    eta: datetime | None = None
    stage: str = Field(default="PLANNED", max_length=10)
    follow_up: bool = False
    remarks: str | None = Field(default=None, max_length=500)
    source: str = Field(default="MANUAL", max_length=10)
    reason: str | None = Field(default=None, max_length=500)


def _clean(body: LegIn, kind: str) -> dict:
    v = body.model_dump(exclude={"reason", "source"})
    v["stage"] = (v["stage"] or "").upper()
    if v["stage"] not in STAGES:
        raise HTTPException(422, "Stage must be Planned or Confirmed")
    for k in ("carrier", "voyage", "pol", "pod", "remarks"):
        v[k] = (v[k] or "").strip() or None
    if kind == "SEA":
        if not v["carrier"]:
            raise HTTPException(422, "Enter the vessel name")
        if v["pol"] and v["pod"] and v["pol"].strip().lower() == v["pod"].strip().lower():
            raise HTTPException(422, "Port of loading and port of discharge cannot be the same")
    else:
        if not v["carrier"] and not v["voyage"]:
            raise HTTPException(422, "Enter the airline or the flight number")
        v["pol"] = v["pod"] = None
    for k in ("etd", "eta"):                                    # ETD and ETA are dates: the time of day is not asked for and not kept
        if v[k] is not None:
            d = (v[k] if v[k].tzinfo else v[k].replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            v[k] = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if v["etd"] and v["eta"] and v["eta"] < v["etd"]:
        raise HTTPException(422, "ETA cannot be earlier than ETD")
    if v["stage"] == "CONFIRMED":
        v["follow_up"] = False                                    # a confirmed schedule needs no follow-up: follow-up belongs to planned legs only
    if v["stage"] == "CONFIRMED" and not (v["etd"] or v["eta"]):
        raise HTTPException(422, "A confirmed leg needs its ETD or ETA")
    return v


def _hist(db: Session, job: Shipment, leg: JobRoutingLeg, user: User, action: str, field: str | None = None, old=None, new=None, remarks: str | None = None) -> None:
    db.add(JobRoutingHistory(organization_id=job.organization_id, shipment_id=job.id, leg_id=leg.id, action=action, field=field, old_value=_fmt(old)[:300] or None, new_value=_fmt(new)[:300] or None,
                             remarks=remarks, user_id=user.id))


def _label(leg: JobRoutingLeg) -> str:
    return (leg.carrier or "") + (" " + leg.voyage if leg.voyage else "")


@router.get("/api/jobs/{job_number:path}/routing/history")
def routing_history(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    names = {u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.organization_id == job.organization_id))}
    legs = {l.id: l for l in db.scalars(select(JobRoutingLeg).where(JobRoutingLeg.shipment_id == job.id))}
    rows = db.scalars(select(JobRoutingHistory).where(JobRoutingHistory.shipment_id == job.id).order_by(JobRoutingHistory.id.desc()).limit(300))
    return [{"at": r.created_at, "by": names.get(r.user_id, "-"), "leg": _label(legs[r.leg_id]) if r.leg_id in legs else "", "action": r.action, "field": LABEL.get(r.field or "", r.field or ""), "from": r.old_value,
             "to": r.new_value, "remarks": r.remarks} for r in rows]


@router.get("/api/jobs/{job_number:path}/routing")
def get_routing(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    kind = _kind(job)
    legs = [_leg(x) for x in _legs(db, job)]
    return {"job_number": job.job_number, "kind": kind, "direction": job.direction, "applicable": kind is not None, "legs": legs, "suggested": [] if legs else suggested(db, job),
            "pol": job.pol, "pod": job.pod, "editable": job.status != "Cancelled" and job.closure_status == "OPEN"}


@router.post("/api/jobs/{job_number:path}/routing/legs", status_code=201)
def add_leg(job_number: str, body: LegIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "its routing")
    kind = _kind(job)
    if kind is None:
        raise HTTPException(422, "Routing applies to Air and Sea jobs")
    v = _clean(body, kind)
    seq = max([x.seq for x in _legs(db, job)] + [0]) + 1
    src = body.source if body.source in ("MANUAL", "JOB", "BOOKING") else "MANUAL"
    leg = JobRoutingLeg(organization_id=job.organization_id, shipment_id=job.id, seq=seq, kind=kind, source=src, created_by=user.id, updated_by=user.id, **v)
    db.add(leg)
    db.flush()
    _hist(db, job, leg, user, "CREATED", remarks=("Started from the " + src.lower()) if src != "MANUAL" else None)
    jl.log_event(db, job, user, "ROUTING_ADDED", "JOB", f"Routing leg {seq} added: {_label(leg).strip() or 'no name yet'} ({leg.stage.title()})", new=leg.stage)
    record_audit(db, "ROUTING_ADDED", "SHIPMENT", user, job.organization_id, "JobRoutingLeg", str(leg.id), {"job_number": job.job_number})
    db.commit()
    return _leg(leg)


def _leg_of(db: Session, job: Shipment, leg_id: int) -> JobRoutingLeg:
    leg = db.get(JobRoutingLeg, leg_id)
    if not leg or leg.shipment_id != job.id or not leg.is_active:
        raise HTTPException(404, "Routing leg not found")
    return leg


@router.put("/api/jobs/{job_number:path}/routing/legs/{leg_id}")
def update_leg(job_number: str, leg_id: int, body: LegIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "its routing")
    leg = _leg_of(db, job, leg_id)
    v = _clean(body, leg.kind)
    changed = []
    for k in TRACKED:
        old, new = getattr(leg, k), v[k]
        if (old or None) != (new or None) and not (old in (None, "", False) and new in (None, "", False)):
            _hist(db, job, leg, user, "UPDATED", k, old, new, body.reason)
            changed.append(LABEL[k])
            setattr(leg, k, new)
    if changed:
        if not leg.follow_up:
            leg.follow_up_status = "OPEN"
        leg.updated_by = user.id
        jl.log_event(db, job, user, "ROUTING_UPDATED", "JOB", f"Routing leg {leg.seq} ({_label(leg).strip()}): " + ", ".join(changed) + " changed", reason=body.reason)
        record_audit(db, "ROUTING_UPDATED", "SHIPMENT", user, job.organization_id, "JobRoutingLeg", str(leg.id), {"job_number": job.job_number, "fields": changed})
    db.commit()
    return _leg(leg)


class FollowIn(BaseModel):
    status: str = Field(pattern="^(OPEN|DONE)$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/api/jobs/{job_number:path}/routing/legs/{leg_id}/follow-up")
def follow_up(job_number: str, leg_id: int, body: FollowIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "its routing")
    leg = _leg_of(db, job, leg_id)
    if not leg.follow_up:
        raise HTTPException(409, "This leg is not marked Follow-up Required")
    _hist(db, job, leg, user, "FOLLOW-UP", "follow_up_status", leg.follow_up_status, body.status, body.note)
    leg.follow_up_status, leg.follow_up_note, leg.updated_by = body.status, (body.note or "").strip() or leg.follow_up_note, user.id
    jl.log_event(db, job, user, "ROUTING_FOLLOW_UP", "JOB", f"Routing leg {leg.seq} follow-up {'done' if body.status == 'DONE' else 'reopened'}" + (f": {body.note}" if body.note else ""))
    db.commit()
    return _leg(leg)


@router.delete("/api/jobs/{job_number:path}/routing/legs/{leg_id}")
def remove_leg(job_number: str, leg_id: int, reason: str = "", user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """A leg is never erased: it is removed from the working routing with a reason, and stays in the history."""
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "its routing")
    leg = _leg_of(db, job, leg_id)
    if len(reason.strip()) < 5:
        raise HTTPException(422, "Give a reason (at least 5 characters) for removing this leg")
    leg.is_active, leg.updated_by = False, user.id
    _hist(db, job, leg, user, "REMOVED", remarks=reason.strip())
    jl.log_event(db, job, user, "ROUTING_REMOVED", "JOB", f"Routing leg {leg.seq} ({_label(leg).strip()}) removed: {reason.strip()}", reason=reason.strip())
    record_audit(db, "ROUTING_REMOVED", "SHIPMENT", user, job.organization_id, "JobRoutingLeg", str(leg.id), {"job_number": job.job_number, "reason": reason.strip()})
    db.commit()
    return {"removed": leg.id}


# ---------------- dashboard alerts ----------------
@router.get("/api/routing/alerts")
def routing_alerts(days: int = 3, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    days = max(1, min(days, 30))
    return compute_alerts(user, db, timedelta(days=days), timedelta(days=days), 14, 3)


def compute_alerts(user: User, db: Session, etd_lead: timedelta, eta_lead: timedelta, planned_days: int, changed_days: int) -> list[dict]:
    """One line per leg that needs attention, computed from the saved legs: overdue, follow-up open, planned (not yet confirmed) soon, ETD / ETA coming up (the lead time is a company setting),
    schedule recently changed."""
    if user.organization_id is None:
        raise HTTPException(403, "Routing is tenant-scoped")
    now = _now()
    today = now.date()
    plan_h, changed_since = today + timedelta(days=planned_days), now - timedelta(days=changed_days)
    dd = lambda v: v.astimezone(timezone.utc).date() if v else None
    q = select(JobRoutingLeg, Shipment).join(Shipment, Shipment.id == JobRoutingLeg.shipment_id).where(JobRoutingLeg.organization_id == user.organization_id, JobRoutingLeg.is_active.is_(True),
                                                                                                         Shipment.status.notin_(("Cancelled", "Closed")), Shipment.closure_status == "OPEN")
    clause = shipment_clause(db, user)
    if clause is not None:
        q = q.where(clause)
    rows = db.execute(q).all()
    ids = [l.id for l, _ in rows]
    recent = {}
    if ids:
        for h in db.scalars(select(JobRoutingHistory).where(JobRoutingHistory.leg_id.in_(ids), JobRoutingHistory.action == "UPDATED", JobRoutingHistory.field.in_(("etd", "eta", "carrier", "voyage")), JobRoutingHistory.created_at >= changed_since)):
            recent.setdefault(h.leg_id, set()).add(LABEL.get(h.field, h.field))
    names = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.organization_id == user.organization_id))}
    out = []
    for leg, job in rows:
        flags = []
        if leg.etd and dd(leg.etd) < today and leg.stage == "PLANNED":
            flags.append(("OVERDUE", 0, "ETD has passed and the leg is still Planned"))
        if leg.eta and dd(leg.eta) < today:
            flags.append(("OVERDUE", 0, "ETA has passed"))
        if leg.follow_up and leg.follow_up_status == "OPEN" and leg.stage == "PLANNED":
            flags.append(("FOLLOW_UP", 1, "Follow-up required" + (f": {leg.follow_up_note}" if leg.follow_up_note else "")))
        if leg.stage == "PLANNED" and (leg.etd is None or dd(leg.etd) <= plan_h):
            flags.append(("PLANNED", 2, "Planned - confirmation pending"))
        if leg.etd and dd(leg.etd) >= today and leg.etd - etd_lead <= now:               # ETD / ETA are dates (midnight): the alert starts "lead" before that date begins
            flags.append(("ETD", 3, "ETD coming up"))
        if leg.eta and dd(leg.eta) >= today and leg.eta - eta_lead <= now:
            flags.append(("ETA", 3, "ETA coming up"))
        if leg.id in recent:
            flags.append(("CHANGED", 4, "Schedule changed (" + ", ".join(sorted(recent[leg.id])) + ")"))
        if not flags:
            continue
        flags.sort(key=lambda f: f[1])
        seen, kinds, texts = set(), [], []
        for k, _, t in flags:
            if t not in seen:
                seen.add(t); texts.append(t)
            if k not in kinds:
                kinds.append(k)
        out.append({"leg_id": leg.id, "job_number": job.job_number, "customer": names.get(job.customer_id, ""), "kind": leg.kind, "direction": job.direction, "carrier": leg.carrier, "voyage": leg.voyage,
                    "pol": leg.pol, "pod": leg.pod, "etd": leg.etd, "eta": leg.eta, "stage": leg.stage, "follow_up": leg.follow_up and leg.follow_up_status == "OPEN" and leg.stage == "PLANNED", "flags": kinds,
                    "severity": flags[0][1], "message": " - ".join(texts), "seq": leg.seq})
    out.sort(key=lambda a: (a["severity"], a["etd"] or a["eta"] or now))
    return out[:200]
