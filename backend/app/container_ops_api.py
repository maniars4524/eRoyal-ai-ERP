"""Container operations: surveys, damage register, repair work orders (M&R), leases and on / off-hire, EIR, depot stock counts and alerts.

Builds on containers_api: the container's condition, availability and lease status change only through these controlled workflows, and every change is written to
the container's status history. Reference lists (survey types, damage types, repair categories, conditions) come from Masters > Inventory.
"""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number
from .containers_api import _assets, _get, _history, _master, _now, _org, _require_master, normalise_number
from .db import get_db
from .models import (Container, ContainerDamage, ContainerEIR, ContainerLease, ContainerLeaseItem, ContainerMovement, ContainerRepairOrder, ContainerStockCount, ContainerSurvey, User)

survey_router = APIRouter(prefix="/api/container-surveys", tags=["container surveys"])
damage_router = APIRouter(prefix="/api/container-damages", tags=["container damages"])
repair_router = APIRouter(prefix="/api/container-repairs", tags=["container repairs"])
lease_router = APIRouter(prefix="/api/container-leases", tags=["container leases"])
eir_router = APIRouter(prefix="/api/container-eirs", tags=["container EIR"])
count_router = APIRouter(prefix="/api/container-counts", tags=["container stock counts"])
alert_router = APIRouter(prefix="/api/container-alerts", tags=["container alerts"])

Money = Decimal


def _m(v):
    """Money goes out as an exact string, never as a float."""
    return None if v is None else str(v)


SEVERITY = "^(Minor|Moderate|Major|Severe)$"


def _sod_blocked(db: Session, user: User, org: int, created_by: int | None) -> bool:
    """Deciding your own record is refused while another active user can decide it; a one-person company must still be able to work."""
    if created_by != user.id:
        return False
    others = [u for u in db.scalars(select(User).where(User.organization_id == org, User.status == "ACTIVE", User.id != user.id))
              if u.is_super_admin or any(p.code == "container.approve" for r in u.roles for p in r.permissions)]
    return bool(others)


def _require_approver_distinct(db, user, org, created_by, what):
    if _sod_blocked(db, user, org, created_by):
        raise HTTPException(403, f"You created this {what}; another approver must decide it")


def _cname(db: Session, container_id: int) -> str | None:
    return db.scalar(select(Container.container_number).where(Container.id == container_id))


def _names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    return {c.id: c.container_number for c in db.scalars(select(Container).where(Container.id.in_(ids)))} if ids else {}


def _page(q, db, limit, offset, order):
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    return total, db.scalars(q.order_by(order).limit(limit).offset(offset)).all()


# ================================================================ surveys
class ChecklistRow(BaseModel):
    item: str = Field(min_length=1, max_length=120)
    result: str = Field(pattern="^(OK|DAMAGED|NA)$")
    remarks: str | None = Field(default=None, max_length=300)


class SurveyIn(BaseModel):
    container_id: int
    survey_type: str = Field(max_length=120)
    survey_date: date
    depot: str | None = Field(default=None, max_length=120)
    surveyor: str | None = Field(default=None, max_length=120)
    checklist: list[ChecklistRow] = Field(default_factory=list, max_length=80)
    damage_location: str | None = Field(default=None, max_length=200)
    damage_description: str | None = Field(default=None, max_length=2000)
    severity: str | None = Field(default=None, pattern=SEVERITY)
    recommended_repair: str | None = Field(default=None, max_length=1000)
    repair_estimate: Money | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=1000)


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _survey_out(s: ContainerSurvey, number: str | None = None) -> dict:
    return {"id": s.id, "number": s.number, "container_id": s.container_id, "container_number": number, "survey_type": s.survey_type, "survey_date": s.survey_date, "depot": s.depot, "surveyor": s.surveyor,
            "checklist": s.checklist or [], "damage_location": s.damage_location, "damage_description": s.damage_description, "severity": s.severity, "recommended_repair": s.recommended_repair,
            "repair_estimate": _m(s.repair_estimate), "remarks": s.remarks, "status": s.status, "decision_note": s.decision_note, "decided_at": s.decided_at, "created_at": s.created_at}


@survey_router.get("")
def list_surveys(q: str | None = None, status: str | None = Query(default=None, pattern="^(DRAFT|SUBMITTED|APPROVED|REJECTED)$"), container_id: int | None = None, survey_type: str | None = None,
                 limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [ContainerSurvey.organization_id == org]
    if status:
        w.append(ContainerSurvey.status == status)
    if container_id:
        w.append(ContainerSurvey.container_id == container_id)
    if survey_type:
        w.append(ContainerSurvey.survey_type == survey_type)
    base = select(ContainerSurvey).where(*w)
    if q:
        base = base.join(Container, Container.id == ContainerSurvey.container_id).where(or_(Container.container_number.ilike(f"%{normalise_number(q) or q}%"), ContainerSurvey.number.ilike(f"%{q}%")))
    total, rows = _page(base, db, limit, offset, ContainerSurvey.id.desc())
    n = _names(db, [r.container_id for r in rows])
    return {"items": [_survey_out(r, n.get(r.container_id)) for r in rows], "total": total}


@survey_router.post("", status_code=201)
def create_survey(body: SurveyIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, body.container_id)
    _require_master(db, org, "inv_survey_type", body.survey_type, "Survey type")
    if body.depot:
        _require_master(db, org, "inv_depot", body.depot, "Depot")
    s = ContainerSurvey(organization_id=org, created_by=user.id, **body.model_dump(exclude={"checklist"}), checklist=[r.model_dump() for r in body.checklist])
    s.depot = body.depot or c.depot
    s.number = next_number(db, org, "container_survey", "SRV", body.survey_date, user=user)
    db.add(s)
    db.flush()
    record_audit(db, "CONTAINER_SURVEY_CREATED", "INVENTORY", user, org, "ContainerSurvey", str(s.id), {"number": s.number, "container": c.container_number})
    db.commit()
    return _survey_out(s, c.container_number)


def _survey(db, org, sid, lock=False) -> ContainerSurvey:
    q = select(ContainerSurvey).where(ContainerSurvey.id == sid, ContainerSurvey.organization_id == org)
    s = db.scalar(q.with_for_update() if lock else q)
    if not s:
        raise HTTPException(404, "Survey not found")
    return s


@survey_router.get("/{survey_id}")
def get_survey(survey_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    s = _survey(db, org, survey_id)
    return _survey_out(s, _cname(db, s.container_id))


@survey_router.patch("/{survey_id}")
def update_survey(survey_id: int, body: SurveyIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    s = _survey(db, org, survey_id, lock=True)
    if s.status != "DRAFT":
        raise HTTPException(409, "Only a draft survey can be edited")
    for k, v in body.model_dump(exclude={"checklist", "container_id"}).items():
        setattr(s, k, v)
    s.checklist = [r.model_dump() for r in body.checklist]
    db.commit()
    return _survey_out(s, _cname(db, s.container_id))


@survey_router.post("/{survey_id}/submit")
def submit_survey(survey_id: int, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    s = _survey(db, org, survey_id, lock=True)
    if s.status != "DRAFT":
        raise HTTPException(409, f"This survey is already {s.status.lower()}")
    if not s.checklist and not s.damage_description:
        raise HTTPException(422, "Fill in the checklist or describe the condition before submitting")
    s.status = "SUBMITTED"
    db.commit()
    return _survey_out(s, _cname(db, s.container_id))


@survey_router.post("/{survey_id}/approve")
def approve_survey(survey_id: int, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    """Approving a survey that found damage opens a damage record for the container."""
    org = _org(user)
    s = _survey(db, org, survey_id, lock=True)
    if s.status != "SUBMITTED":
        raise HTTPException(409, "Only a submitted survey can be approved")
    _require_approver_distinct(db, user, org, s.created_by, "survey")
    s.status, s.decided_by, s.decided_at = "APPROVED", user.id, _now()
    damaged = [r for r in (s.checklist or []) if r.get("result") == "DAMAGED"]
    out = _survey_out(s, _cname(db, s.container_id))
    if damaged or s.severity or s.damage_description:
        dmg = ContainerDamage(organization_id=org, container_id=s.container_id, survey_id=s.id, damage_date=s.survey_date, damage_location=s.damage_location or ", ".join(r["item"] for r in damaged)[:200] or None,
                              severity=s.severity, description=s.damage_description or "; ".join(f"{r['item']}: {r.get('remarks') or 'damaged'}" for r in damaged)[:2000], depot=s.depot,
                              estimated_cost=s.repair_estimate, created_by=user.id)
        dmg.number = next_number(db, org, "container_damage", "DMG", s.survey_date, user=user)
        db.add(dmg)
        db.flush()
        out["damage_id"], out["damage_number"] = dmg.id, dmg.number
    record_audit(db, "CONTAINER_SURVEY_APPROVED", "INVENTORY", user, org, "ContainerSurvey", str(s.id), {"number": s.number})
    db.commit()
    return out


@survey_router.post("/{survey_id}/reject")
def reject_survey(survey_id: int, body: Reason, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    s = _survey(db, org, survey_id, lock=True)
    if s.status != "SUBMITTED":
        raise HTTPException(409, "Only a submitted survey can be rejected")
    s.status, s.decided_by, s.decided_at, s.decision_note = "REJECTED", user.id, _now(), body.reason
    db.commit()
    return _survey_out(s, _cname(db, s.container_id))


# ================================================================ damages
class DamageIn(BaseModel):
    container_id: int
    damage_date: date
    damage_location: str | None = Field(default=None, max_length=200)
    damage_type: str | None = Field(default=None, max_length=120)
    severity: str | None = Field(default=None, pattern=SEVERITY)
    description: str | None = Field(default=None, max_length=2000)
    responsible_party: str | None = Field(default=None, max_length=160)
    estimated_cost: Money | None = Field(default=None, ge=0)


class DamagePatch(BaseModel):
    responsible_party: str | None = Field(default=None, max_length=160)
    claim_status: str | None = Field(default=None, pattern="^(NONE|RAISED|SETTLED|REJECTED)$")
    estimated_cost: Money | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)


def _dmg_out(d: ContainerDamage, number: str | None = None) -> dict:
    return {"id": d.id, "number": d.number, "container_id": d.container_id, "container_number": number, "survey_id": d.survey_id, "damage_date": d.damage_date, "damage_location": d.damage_location,
            "damage_type": d.damage_type, "severity": d.severity, "description": d.description, "responsible_party": d.responsible_party, "depot": d.depot, "estimated_cost": _m(d.estimated_cost),
            "actual_cost": _m(d.actual_cost), "claim_status": d.claim_status, "status": d.status, "created_at": d.created_at}


@damage_router.get("")
def list_damages(q: str | None = None, status: str | None = Query(default=None, pattern="^(OPEN|IN_REPAIR|CLOSED)$"), container_id: int | None = None, limit: int = Query(default=50, ge=1, le=500),
                 offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [ContainerDamage.organization_id == org]
    if status:
        w.append(ContainerDamage.status == status)
    if container_id:
        w.append(ContainerDamage.container_id == container_id)
    base = select(ContainerDamage).where(*w)
    if q:
        base = base.join(Container, Container.id == ContainerDamage.container_id).where(or_(Container.container_number.ilike(f"%{normalise_number(q) or q}%"), ContainerDamage.number.ilike(f"%{q}%")))
    total, rows = _page(base, db, limit, offset, ContainerDamage.id.desc())
    n = _names(db, [r.container_id for r in rows])
    return {"items": [_dmg_out(r, n.get(r.container_id)) for r in rows], "total": total}


@damage_router.post("", status_code=201)
def create_damage(body: DamageIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, body.container_id)
    if body.damage_type:
        _require_master(db, org, "inv_damage_type", body.damage_type, "Damage type")
    d = ContainerDamage(organization_id=org, depot=c.depot, created_by=user.id, **body.model_dump(exclude={"container_id"}), container_id=c.id)
    d.number = next_number(db, org, "container_damage", "DMG", body.damage_date, user=user)
    db.add(d)
    db.commit()
    return _dmg_out(d, c.container_number)


@damage_router.patch("/{damage_id}")
def update_damage(damage_id: int, body: DamagePatch, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    d = db.scalar(select(ContainerDamage).where(ContainerDamage.id == damage_id, ContainerDamage.organization_id == org).with_for_update())
    if not d:
        raise HTTPException(404, "Damage record not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    db.commit()
    return _dmg_out(d, _cname(db, d.container_id))


# ================================================================ repair work orders
class RepairIn(BaseModel):
    container_id: int
    damage_id: int | None = None
    category: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    vendor: str | None = Field(default=None, max_length=160)
    workshop: str | None = Field(default=None, max_length=160)
    labour: Money = Field(default=Decimal(0), ge=0)
    material: Money = Field(default=Decimal(0), ge=0)
    welding: Money = Field(default=Decimal(0), ge=0)
    painting: Money = Field(default=Decimal(0), ge=0)
    cleaning: Money = Field(default=Decimal(0), ge=0)
    transport: Money = Field(default=Decimal(0), ge=0)
    other: Money = Field(default=Decimal(0), ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    warranty_days: int | None = Field(default=None, ge=0)


class CompleteIn(BaseModel):
    actual_cost: Money = Field(ge=0)
    completion_date: date


class QcIn(BaseModel):
    condition_after: str = Field(max_length=80)
    note: str | None = Field(default=None, max_length=500)


class SupplementIn(BaseModel):
    amount: Money = Field(gt=0)
    reason: str = Field(min_length=3, max_length=300)


PARTS = ("labour", "material", "welding", "painting", "cleaning", "transport", "other")


def _repair_out(r: ContainerRepairOrder, number: str | None = None) -> dict:
    approved_supp = sum((Decimal(str(s["amount"])) for s in (r.supplements or []) if s.get("status") == "APPROVED"), Decimal(0))
    limit = (r.approved_amount + approved_supp) if r.approved_amount is not None else None
    return {"id": r.id, "number": r.number, "container_id": r.container_id, "container_number": number, "damage_id": r.damage_id, "category": r.category, "description": r.description, "vendor": r.vendor,
            "workshop": r.workshop, **{p: _m(getattr(r, p)) for p in PARTS}, "currency": r.currency, "estimated_total": _m(r.estimated_total), "approved_amount": _m(r.approved_amount), "approved_with_supplements": _m(limit),
            "actual_cost": _m(r.actual_cost), "over_approved_amount": bool(limit is not None and r.actual_cost is not None and r.actual_cost > limit), "supplements": r.supplements or [],
            "start_date": r.start_date, "completion_date": r.completion_date, "downtime_days": r.downtime_days, "warranty_days": r.warranty_days, "condition_after": r.condition_after,
            "status": r.status, "status_note": r.status_note, "created_at": r.created_at}


def _repair(db, org, rid, lock=False) -> ContainerRepairOrder:
    q = select(ContainerRepairOrder).where(ContainerRepairOrder.id == rid, ContainerRepairOrder.organization_id == org)
    r = db.scalar(q.with_for_update() if lock else q)
    if not r:
        raise HTTPException(404, "Repair work order not found")
    return r


def _open_repairs(db, container_id, exclude=None) -> int:
    q = select(func.count()).select_from(ContainerRepairOrder).where(ContainerRepairOrder.container_id == container_id, ContainerRepairOrder.status.in_(["APPROVED", "IN_PROGRESS", "COMPLETED"]))
    if exclude:
        q = q.where(ContainerRepairOrder.id != exclude)
    return db.scalar(q) or 0


def _set_availability(db, c: Container, new: str, user, reason: str):
    if c.availability != new:
        _history(db, c, "availability", c.availability, new, user, reason)
        c.availability = new


@repair_router.get("/summary")
def repair_summary(user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    """Repair cost and estimate-versus-actual figures from real work orders."""
    org = _org(user)
    rows = db.scalars(select(ContainerRepairOrder).where(ContainerRepairOrder.organization_id == org)).all()
    by_status: dict[str, int] = {}
    by_vendor: dict[str, Decimal] = {}
    est = act = Decimal(0)
    done = [r for r in rows if r.actual_cost is not None]
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    for r in done:
        by_vendor[r.vendor or "Not set"] = by_vendor.get(r.vendor or "Not set", Decimal(0)) + r.actual_cost
        est += r.estimated_total or 0
        act += r.actual_cost
    avg_down = [r.downtime_days for r in rows if r.downtime_days is not None]
    return {"orders": len(rows), "by_status": by_status, "cost_by_vendor": {k: str(v) for k, v in by_vendor.items()}, "estimated_of_completed": str(est), "actual_of_completed": str(act),
            "average_downtime_days": round(sum(avg_down) / len(avg_down), 1) if avg_down else None, "waiting_approval": by_status.get("ESTIMATED", 0)}


@repair_router.get("")
def list_repairs(q: str | None = None, status: str | None = None, container_id: int | None = None, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
                 user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [ContainerRepairOrder.organization_id == org]
    if status:
        w.append(ContainerRepairOrder.status == status)
    if container_id:
        w.append(ContainerRepairOrder.container_id == container_id)
    base = select(ContainerRepairOrder).where(*w)
    if q:
        base = base.join(Container, Container.id == ContainerRepairOrder.container_id).where(or_(Container.container_number.ilike(f"%{normalise_number(q) or q}%"), ContainerRepairOrder.number.ilike(f"%{q}%")))
    total, rows = _page(base, db, limit, offset, ContainerRepairOrder.id.desc())
    n = _names(db, [r.container_id for r in rows])
    return {"items": [_repair_out(r, n.get(r.container_id)) for r in rows], "total": total}


@repair_router.post("", status_code=201)
def create_repair(body: RepairIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, body.container_id)
    if c.location_status == "DISPOSED":
        raise HTTPException(409, "A disposed container cannot be repaired")
    if body.category:
        _require_master(db, org, "inv_repair_category", body.category, "Repair category")
    if body.damage_id and not db.scalar(select(ContainerDamage.id).where(ContainerDamage.id == body.damage_id, ContainerDamage.container_id == c.id, ContainerDamage.organization_id == org)):
        raise HTTPException(404, "That damage record does not belong to this container")
    r = ContainerRepairOrder(organization_id=org, created_by=user.id, **body.model_dump())
    r.estimated_total = sum((getattr(r, p) for p in PARTS), Decimal(0))
    if r.estimated_total <= 0:
        raise HTTPException(422, "Enter at least one estimated cost")
    r.number = next_number(db, org, "container_repair", "CWO", date.today(), user=user)
    db.add(r)
    db.flush()
    record_audit(db, "CONTAINER_REPAIR_CREATED", "INVENTORY", user, org, "ContainerRepairOrder", str(r.id), {"number": r.number, "container": c.container_number, "estimate": str(r.estimated_total)})
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.get("/{repair_id}")
def get_repair(repair_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    r = _repair(db, org, repair_id)
    return _repair_out(r, _cname(db, r.container_id))


def _transition(repair_id, allowed_from, user, db, perm="container.approve"):
    org = _org(user)
    r = _repair(db, org, repair_id, lock=True)
    if r.status not in allowed_from:
        raise HTTPException(409, f"A work order that is {r.status.lower().replace('_', ' ')} cannot do this")
    return org, r, _get(db, org, r.container_id, lock=True)


@repair_router.post("/{repair_id}/approve")
def approve_repair(repair_id: int, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("ESTIMATED",), user, db)
    _require_approver_distinct(db, user, org, r.created_by, "work order")
    r.status, r.approved_by, r.approved_amount = "APPROVED", user.id, r.estimated_total
    if c.location_status == "AT_DEPOT" and c.availability == "AVAILABLE":
        _set_availability(db, c, "UNDER_REPAIR", user, f"Repair {r.number} approved")             # a repair-pending container is no longer available
    if r.damage_id:
        d = db.get(ContainerDamage, r.damage_id)
        if d and d.status == "OPEN":
            d.status = "IN_REPAIR"
    record_audit(db, "CONTAINER_REPAIR_APPROVED", "INVENTORY", user, org, "ContainerRepairOrder", str(r.id), {"number": r.number, "amount": str(r.approved_amount)})
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/reject")
def reject_repair(repair_id: int, body: Reason, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("ESTIMATED",), user, db)
    r.status, r.status_note = "REJECTED", body.reason
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/start")
def start_repair(repair_id: int, start_date: date | None = None, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("APPROVED",), user, db)
    if c.location_status not in ("AT_DEPOT", "AT_REPAIR"):
        raise HTTPException(409, f"Container {c.container_number} must be at a depot or at the repair yard to start the repair (it is {c.location_status.replace('_', ' ').lower()})")
    r.status, r.start_date = "IN_PROGRESS", start_date or date.today()
    _set_availability(db, c, "UNDER_REPAIR", user, f"Repair {r.number} started")
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/complete")
def complete_repair(repair_id: int, body: CompleteIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("IN_PROGRESS",), user, db)
    if body.completion_date < (r.start_date or body.completion_date):
        raise HTTPException(422, "Completion date is before the start date")
    r.status, r.actual_cost, r.completion_date = "COMPLETED", body.actual_cost, body.completion_date
    r.downtime_days = (body.completion_date - (r.start_date or body.completion_date)).days + 1
    if r.damage_id:
        d = db.get(ContainerDamage, r.damage_id)
        if d:
            d.actual_cost = body.actual_cost
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/qc")
def quality_approve_repair(repair_id: int, body: QcIn, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    """Post-repair quality approval: the container takes its new condition and, if it is at a depot and the condition allows, becomes available again."""
    org, r, c = _transition(repair_id, ("COMPLETED",), user, db)
    cond = _require_master(db, org, "inv_container_condition", body.condition_after, "Condition")
    r.status, r.qc_by, r.condition_after, r.status_note = "QC_APPROVED", user.id, cond.name, body.note
    _history(db, c, "condition", c.condition, cond.name, user, f"Repair {r.number} quality approved")
    c.condition = cond.name
    if not _open_repairs(db, c.id, exclude=r.id) and c.location_status == "AT_DEPOT":
        _set_availability(db, c, "NOT_AVAILABLE" if (cond.data or {}).get("available_for_use") == "No" else "AVAILABLE", user, f"Repair {r.number} quality approved")
    elif c.location_status == "AT_REPAIR":
        _set_availability(db, c, "NOT_AVAILABLE", user, f"Repair {r.number} done; container returns to a depot first")
    if r.damage_id:
        d = db.get(ContainerDamage, r.damage_id)
        if d:
            d.status = "CLOSED"
    record_audit(db, "CONTAINER_REPAIR_QC", "INVENTORY", user, org, "ContainerRepairOrder", str(r.id), {"number": r.number, "condition": cond.name})
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/cancel")
def cancel_repair(repair_id: int, body: Reason, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("ESTIMATED", "APPROVED", "IN_PROGRESS"), user, db)
    if (r.status != "ESTIMATED" or r.created_by != user.id) and not has_permission(user, "container.approve"):
        raise HTTPException(403, "Only an approver can cancel an approved work order")
    was = r.status
    r.status, r.status_note = "CANCELLED", body.reason
    if was in ("APPROVED", "IN_PROGRESS") and not _open_repairs(db, c.id, exclude=r.id) and c.availability == "UNDER_REPAIR":
        _set_availability(db, c, "NOT_AVAILABLE", user, f"Repair {r.number} cancelled: needs a condition check before use")
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/reopen")
def reopen_repair(repair_id: int, body: Reason, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    """Quality check failed (COMPLETED -> IN_PROGRESS) or a rejected estimate is reconsidered (REJECTED -> ESTIMATED)."""
    org, r, c = _transition(repair_id, ("COMPLETED", "REJECTED"), user, db)
    if r.status == "COMPLETED":
        r.status = "IN_PROGRESS"
        _set_availability(db, c, "UNDER_REPAIR", user, f"Repair {r.number} reopened: {body.reason}")
    else:
        r.status = "ESTIMATED"
    r.status_note = body.reason
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/supplement")
def add_supplement(repair_id: int, body: SupplementIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org, r, c = _transition(repair_id, ("APPROVED", "IN_PROGRESS", "COMPLETED"), user, db)
    r.supplements = list(r.supplements or []) + [{"amount": str(body.amount), "reason": body.reason, "status": "PENDING", "by": user.id}]
    db.commit()
    return _repair_out(r, c.container_number)


@repair_router.post("/{repair_id}/supplement/{index}/{decision}")
def decide_supplement(repair_id: int, index: int, decision: str, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "Not found")
    org, r, c = _transition(repair_id, ("APPROVED", "IN_PROGRESS", "COMPLETED"), user, db)
    items = [dict(x) for x in (r.supplements or [])]
    if not 0 <= index < len(items) or items[index]["status"] != "PENDING":
        raise HTTPException(404, "No pending supplementary estimate with that number")
    if items[index].get("by") == user.id and _sod_blocked(db, user, org, user.id):
        raise HTTPException(403, "You raised this supplementary estimate; another approver must decide it")
    items[index]["status"] = "APPROVED" if decision == "approve" else "REJECTED"
    r.supplements = items
    db.commit()
    return _repair_out(r, c.container_number)


# ================================================================ leases
class LeaseIn(BaseModel):
    agreement_number: str = Field(min_length=1, max_length=60)
    leasing_company: str = Field(min_length=1, max_length=160)
    start_date: date
    end_date: date | None = None
    daily_rental: Money | None = Field(default=None, ge=0)
    monthly_rental: Money | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    free_days: int = Field(default=0, ge=0)
    min_lease_days: int | None = Field(default=None, ge=0)
    deposit: Money | None = Field(default=None, ge=0)
    redelivery_location: str | None = Field(default=None, max_length=160)
    redelivery_conditions: str | None = Field(default=None, max_length=1000)


class OnHireIn(BaseModel):
    container_id: int
    on_hire_date: date
    daily_rate: Money | None = Field(default=None, ge=0)


class OffHireIn(BaseModel):
    off_hire_date: date


class ExtendIn(BaseModel):
    new_end_date: date
    reason: str = Field(min_length=3, max_length=300)


def _lease(db, org, lid, lock=False) -> ContainerLease:
    q = select(ContainerLease).where(ContainerLease.id == lid, ContainerLease.organization_id == org)
    l = db.scalar(q.with_for_update() if lock else q)
    if not l:
        raise HTTPException(404, "Lease not found")
    return l


def _item_cost(l: ContainerLease, it: ContainerLeaseItem, today: date) -> dict:
    end = it.off_hire_date or today
    total = max((end - it.on_hire_date).days + 1, 0)
    billable = max(total - (l.free_days or 0), 0)
    if l.min_lease_days and total < l.min_lease_days:
        billable = max(billable, l.min_lease_days)
    rate = it.daily_rate if it.daily_rate is not None else (l.daily_rental if l.daily_rental is not None else ((l.monthly_rental or Decimal(0)) / Decimal(30) if l.monthly_rental else None))
    return {"days": total, "free_days": min(total, l.free_days or 0), "billable_days": billable, "rate": rate, "amount": (rate * billable).quantize(Decimal("0.01")) if rate is not None else None}


def _lease_out(db, l: ContainerLease, with_items=False) -> dict:
    items = db.scalars(select(ContainerLeaseItem).where(ContainerLeaseItem.lease_id == l.id).order_by(ContainerLeaseItem.id)).all()
    on = [i for i in items if i.status == "ON_HIRE"]
    today = date.today()
    costs = [_item_cost(l, i, today) for i in items]
    out = {"id": l.id, "agreement_number": l.agreement_number, "leasing_company": l.leasing_company, "start_date": l.start_date, "end_date": l.end_date, "daily_rental": _m(l.daily_rental),
           "monthly_rental": _m(l.monthly_rental), "currency": l.currency, "free_days": l.free_days, "min_lease_days": l.min_lease_days, "deposit": _m(l.deposit), "redelivery_location": l.redelivery_location,
           "redelivery_conditions": l.redelivery_conditions, "extensions": l.extensions or [], "status": l.status, "containers_on_hire": len(on), "containers_total": len(items),
           "overdue_redelivery": bool(l.end_date and l.end_date < today and on), "accrued_cost": str(sum((c["amount"] or Decimal(0) for c in costs), Decimal(0)))}
    if with_items:
        n = _names(db, [i.container_id for i in items])
        out["items"] = [{"id": i.id, "container_id": i.container_id, "container_number": n.get(i.container_id), "on_hire_date": i.on_hire_date, "off_hire_date": i.off_hire_date, "status": i.status, **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in c.items()}}
                        for i, c in zip(items, costs)]
    return out


@lease_router.get("")
def list_leases(status: str | None = Query(default=None, pattern="^(ACTIVE|CLOSED)$"), limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
                user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    base = select(ContainerLease).where(ContainerLease.organization_id == org, *([ContainerLease.status == status] if status else []))
    total, rows = _page(base, db, limit, offset, ContainerLease.id.desc())
    return {"items": [_lease_out(db, l) for l in rows], "total": total}


@lease_router.post("", status_code=201)
def create_lease(body: LeaseIn, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    if body.end_date and body.end_date < body.start_date:
        raise HTTPException(422, "End date is before the start date")
    if db.scalar(select(ContainerLease.id).where(ContainerLease.organization_id == org, ContainerLease.agreement_number == body.agreement_number)):
        raise HTTPException(409, f"Lease agreement {body.agreement_number} already exists")
    l = ContainerLease(organization_id=org, created_by=user.id, **body.model_dump())
    db.add(l)
    db.flush()
    record_audit(db, "CONTAINER_LEASE_CREATED", "INVENTORY", user, org, "ContainerLease", str(l.id), {"agreement": l.agreement_number, "company": l.leasing_company})
    db.commit()
    return _lease_out(db, l, True)


@lease_router.get("/{lease_id}")
def get_lease(lease_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    return _lease_out(db, _lease(db, _org(user), lease_id), True)


@lease_router.post("/{lease_id}/on-hire", status_code=201)
def on_hire(lease_id: int, body: OnHireIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    l = _lease(db, org, lease_id, lock=True)
    c = _get(db, org, body.container_id, lock=True)
    if l.status != "ACTIVE":
        raise HTTPException(409, "This lease is closed")
    if _assets(db, org).get((c.ownership or "").lower()):
        raise HTTPException(409, f"Container {c.container_number} is recorded as a company fixed asset; a leased container cannot be capitalised as company-owned. Change its ownership first")
    if c.location_status == "DISPOSED":
        raise HTTPException(409, "A disposed container cannot go on hire")
    if db.scalar(select(ContainerLeaseItem.id).where(ContainerLeaseItem.container_id == c.id, ContainerLeaseItem.status == "ON_HIRE")):
        raise HTTPException(409, f"Container {c.container_number} is already on hire")
    if body.on_hire_date < l.start_date:
        raise HTTPException(422, "On-hire date is before the lease start date")
    it = ContainerLeaseItem(organization_id=org, lease_id=l.id, container_id=c.id, on_hire_date=body.on_hire_date, daily_rate=body.daily_rate, created_by=user.id)
    db.add(it)
    _history(db, c, "lease_status", c.lease_status, "ON_HIRE", user, f"On hire under {l.agreement_number}")
    c.lease_status = "ON_HIRE"
    db.commit()
    return _lease_out(db, l, True)


@lease_router.post("/items/{item_id}/off-hire")
def off_hire(item_id: int, body: OffHireIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    it = db.scalar(select(ContainerLeaseItem).where(ContainerLeaseItem.id == item_id, ContainerLeaseItem.organization_id == org).with_for_update())
    if not it:
        raise HTTPException(404, "Lease item not found")
    if it.status != "ON_HIRE":
        raise HTTPException(409, "This container is already off hire")
    if body.off_hire_date < it.on_hire_date:
        raise HTTPException(422, "Off-hire date is before the on-hire date")
    c = _get(db, org, it.container_id, lock=True)
    it.status, it.off_hire_date = "OFF_HIRE", body.off_hire_date
    _history(db, c, "lease_status", c.lease_status, "OFF_HIRE", user, "Off hire")
    c.lease_status = "OFF_HIRE"
    db.commit()
    return _lease_out(db, _lease(db, org, it.lease_id), True)


@lease_router.post("/{lease_id}/extend")
def extend_lease(lease_id: int, body: ExtendIn, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    l = _lease(db, org, lease_id, lock=True)
    if l.status != "ACTIVE":
        raise HTTPException(409, "This lease is closed")
    if l.end_date and body.new_end_date <= l.end_date:
        raise HTTPException(422, "The new end date must be later than the current one")
    l.extensions = list(l.extensions or []) + [{"old_end": str(l.end_date) if l.end_date else None, "new_end": str(body.new_end_date), "reason": body.reason, "by": user.id, "at": _now().isoformat()}]
    l.end_date = body.new_end_date
    db.commit()
    return _lease_out(db, l, True)


@lease_router.post("/{lease_id}/close")
def close_lease(lease_id: int, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    l = _lease(db, org, lease_id, lock=True)
    if db.scalar(select(ContainerLeaseItem.id).where(ContainerLeaseItem.lease_id == l.id, ContainerLeaseItem.status == "ON_HIRE")):
        raise HTTPException(409, "Containers are still on hire under this lease. Off-hire them first")
    l.status = "CLOSED"
    db.commit()
    return _lease_out(db, l, True)


# ================================================================ EIR
EIR_TEXT = {"load_status": 12, "custom_seal": 40, "line_seal": 40, "shipper_seal": 40, "set_temp": 10, "read_temp": 10, "fuel": 10, "chassis_no": 40, "genset_no": 40, "carrier": 160, "shipping_agent": 160,
            "time_in": 8, "time_out": 8, "container_remarks": 500, "chassis_remarks": 300, "codes_remarks": 300, "customer_signature": 120}
EIR_FLAGS = {"reposition_for_maintenance"}
EIR_RESULTS = {"OK", "DEFECTIVE", "DAMAGE"}


def clean_eir_details(raw: dict | None) -> dict:
    """The printed EIR form: only the known boxes are kept, each with a length limit."""
    raw = raw or {}
    out: dict[str, Any] = {}
    for k, n in EIR_TEXT.items():
        if raw.get(k) not in (None, ""):
            out[k] = str(raw[k]).strip()[:n]
    for k in EIR_FLAGS:
        if raw.get(k):
            out[k] = True
    tires = {k: str(v).strip()[:20] for k, v in (raw.get("tires") or {}).items() if k in ("lo_front", "li_front", "lo_rear", "li_rear", "ro_front", "ri_front", "ro_rear", "ri_rear") and str(v).strip()}
    if tires:
        out["tires"] = tires
    caps = {k: bool(v) for k, v in (raw.get("tattle_caps_missing") or {}).items() if k in ("left_f", "left_r", "right_f", "right_r") and v}
    if caps:
        out["tattle_caps_missing"] = caps
    chk = {str(k)[:60]: v for k, v in (raw.get("chassis_check") or {}).items() if v in EIR_RESULTS}
    if chk:
        out["chassis_check"] = chk
    for key in ("container_codes", "tire_codes"):
        codes = [str(c)[:2] for c in (raw.get(key) or []) if str(c).strip().isdigit()][:30]
        if codes:
            out[key] = codes
    return out


class EirIn(BaseModel):
    movement_id: int
    details: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = Field(default=None, max_length=80)
    damage_description: str | None = Field(default=None, max_length=1000)
    driver_signed_by: str | None = Field(default=None, max_length=120)
    party_signed_by: str | None = Field(default=None, max_length=120)
    remarks: str | None = Field(default=None, max_length=500)


def _eir_view(db: Session, e: ContainerEIR) -> dict:
    m = db.get(ContainerMovement, e.movement_id)
    c = db.get(Container, e.container_id)
    return {"id": e.id, "number": e.number, "movement_number": m.number, "direction": m.direction, "purpose": m.purpose, "container_number": c.container_number, "size": c.size, "container_type": c.container_type,
            "ownership": c.ownership, "from_depot": m.from_depot, "to_depot": m.to_depot, "to_location": m.to_location, "movement_date": m.movement_date, "movement_time": m.movement_time,
            "truck_number": m.truck_number, "driver_name": m.driver_name, "transport_company": m.transport_company, "customer": m.customer, "agent": m.agent, "booking_number": m.booking_number,
            "bl_number": m.bl_number, "job_reference": m.job_reference, "condition": e.condition, "damage_description": e.damage_description, "driver_signed_by": e.driver_signed_by,
            "party_signed_by": e.party_signed_by, "remarks": e.remarks, "details": e.details or {}, "movement_id": e.movement_id, "container_id": e.container_id, "approved_at": m.approved_at, "gate_time": e.gate_time, "created_at": e.created_at}


@eir_router.post("", status_code=201)
def create_eir(body: EirIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    """The EIR is issued for an approved movement, one per movement; the number is written back to the movement."""
    org = _org(user)
    m = db.scalar(select(ContainerMovement).where(ContainerMovement.id == body.movement_id, ContainerMovement.organization_id == org).with_for_update())
    if not m:
        raise HTTPException(404, "Movement not found")
    if m.status != "APPROVED":
        raise HTTPException(409, "An EIR is issued only for an approved movement")
    if db.scalar(select(ContainerEIR.id).where(ContainerEIR.movement_id == m.id)):
        raise HTTPException(409, "This movement already has an EIR")
    e = ContainerEIR(organization_id=org, movement_id=m.id, container_id=m.container_id, gate_time=m.approved_at, condition=body.condition or m.condition_arrival or m.condition_departure,
                     damage_description=body.damage_description or m.damage_remarks, driver_signed_by=body.driver_signed_by, party_signed_by=body.party_signed_by, remarks=body.remarks, details=clean_eir_details(body.details), created_by=user.id)
    e.number = next_number(db, org, "container_eir", "EIR", m.movement_date, user=user)
    db.add(e)
    m.eir_number = e.number
    db.flush()
    record_audit(db, "CONTAINER_EIR_ISSUED", "INVENTORY", user, org, "ContainerEIR", str(e.id), {"number": e.number, "movement": m.number})
    db.commit()
    return _eir_view(db, e)


@eir_router.patch("/{eir_id}")
def correct_eir(eir_id: int, body: EirIn, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    """An issued EIR is corrected only by an approver; the old content is kept in the audit log."""
    org = _org(user)
    e = db.scalar(select(ContainerEIR).where(ContainerEIR.id == eir_id, ContainerEIR.organization_id == org).with_for_update())
    if not e:
        raise HTTPException(404, "EIR not found")
    old = {"condition": e.condition, "damage": e.damage_description, "driver": e.driver_signed_by, "party": e.party_signed_by, "remarks": e.remarks, "details": e.details}
    e.condition, e.damage_description, e.driver_signed_by, e.party_signed_by, e.remarks = body.condition, body.damage_description, body.driver_signed_by, body.party_signed_by, body.remarks
    e.details = clean_eir_details(body.details)
    record_audit(db, "CONTAINER_EIR_CORRECTED", "INVENTORY", user, org, "ContainerEIR", str(e.id), {"number": e.number}, old_value=old)
    db.commit()
    return _eir_view(db, e)


@eir_router.get("/by-movement/{movement_id}")
def eir_for_movement(movement_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    e = db.scalar(select(ContainerEIR).where(ContainerEIR.movement_id == movement_id, ContainerEIR.organization_id == org))
    if not e:
        raise HTTPException(404, "No EIR has been issued for this movement")
    return _eir_view(db, e)


# ================================================================ depot stock count (reconciliation)
class CountIn(BaseModel):
    depot: str = Field(max_length=120)
    count_date: date
    numbers: list[str] = Field(max_length=5000)


def _count_out(k: ContainerStockCount) -> dict:
    return {"id": k.id, "number": k.number, "depot": k.depot, "count_date": k.count_date, "found": k.found or [], "missing": k.missing or [], "unexpected": k.unexpected or [], "status": k.status,
            "review_note": k.review_note, "created_at": k.created_at, "discrepancies": len(k.missing or []) + len(k.unexpected or [])}


@count_router.get("")
def list_counts(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    total, rows = _page(select(ContainerStockCount).where(ContainerStockCount.organization_id == org), db, limit, offset, ContainerStockCount.id.desc())
    return {"items": [_count_out(r) for r in rows], "total": total}


@count_router.post("", status_code=201)
def create_count(body: CountIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    """Compare the numbers found in the yard with what the system says is at the depot. Differences are reported; fixing them is done with movements."""
    org = _org(user)
    dep = _require_master(db, org, "inv_depot", body.depot, "Depot")
    found = sorted({normalise_number(n) for n in body.numbers if normalise_number(n)})
    system = {c.container_number: c for c in db.scalars(select(Container).where(Container.organization_id == org, Container.is_active.is_(True), Container.location_status == "AT_DEPOT", Container.depot == dep.name))}
    registered = {c.container_number: c for c in db.scalars(select(Container).where(Container.organization_id == org, Container.container_number.in_(found)))} if found else {}
    missing = [{"number": n, "note": "System says it is at this depot, but it was not found"} for n in sorted(system) if n not in found]
    unexpected = []
    for n in found:
        if n in system:
            continue
        c = registered.get(n)
        unexpected.append({"number": n, "note": "Not in the register" if not c else f"System says: {c.location_status.replace('_', ' ').lower()}" + (f" at {c.depot}" if c.depot else "")})
    k = ContainerStockCount(organization_id=org, depot=dep.name, count_date=body.count_date, found=found, missing=missing, unexpected=unexpected, created_by=user.id)
    k.number = next_number(db, org, "container_count", "CNT", body.count_date, user=user)
    db.add(k)
    db.flush()
    record_audit(db, "CONTAINER_COUNT_RECORDED", "INVENTORY", user, org, "ContainerStockCount", str(k.id), {"number": k.number, "depot": dep.name, "missing": len(missing), "unexpected": len(unexpected)})
    db.commit()
    return _count_out(k)


@count_router.post("/{count_id}/review")
def review_count(count_id: int, body: Reason, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    k = db.scalar(select(ContainerStockCount).where(ContainerStockCount.id == count_id, ContainerStockCount.organization_id == org).with_for_update())
    if not k:
        raise HTTPException(404, "Stock count not found")
    if k.status == "REVIEWED":
        raise HTTPException(409, "Already reviewed")
    if k.created_by == user.id and _sod_blocked(db, user, org, user.id):
        raise HTTPException(403, "You recorded this count; another approver must review it")
    k.status, k.reviewed_by, k.review_note = "REVIEWED", user.id, body.reason
    db.commit()
    return _count_out(k)


# ================================================================ alerts
@alert_router.get("")
def alerts(days: int = Query(default=60, ge=1, le=730), user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    """Things that need attention, computed from live records."""
    org = _org(user)
    today = date.today()
    soon = today + timedelta(days=days)
    csc = [{"container_number": c.container_number, "container_id": c.id, "csc_valid_until": c.csc_valid_until, "expired": c.csc_valid_until < today, "days_left": (c.csc_valid_until - today).days}
           for c in db.scalars(select(Container).where(Container.organization_id == org, Container.is_active.is_(True), Container.csc_valid_until.is_not(None), Container.csc_valid_until <= soon,
                                                      Container.location_status != "DISPOSED").order_by(Container.csc_valid_until).limit(500))]
    leases = []
    for l in db.scalars(select(ContainerLease).where(ContainerLease.organization_id == org, ContainerLease.status == "ACTIVE", ContainerLease.end_date.is_not(None), ContainerLease.end_date <= today + timedelta(days=days))):
        on = db.scalar(select(func.count()).select_from(ContainerLeaseItem).where(ContainerLeaseItem.lease_id == l.id, ContainerLeaseItem.status == "ON_HIRE")) or 0
        if on:
            leases.append({"lease_id": l.id, "agreement_number": l.agreement_number, "leasing_company": l.leasing_company, "end_date": l.end_date, "days_left": (l.end_date - today).days, "containers_on_hire": on, "overdue_redelivery": l.end_date < today})
    count = lambda model, *w: db.scalar(select(func.count()).select_from(model).where(model.organization_id == org, *w)) or 0
    pending = {"movements_waiting_approval": count(ContainerMovement, ContainerMovement.status == "DRAFT"), "surveys_waiting_approval": count(ContainerSurvey, ContainerSurvey.status == "SUBMITTED"),
               "repairs_waiting_approval": count(ContainerRepairOrder, ContainerRepairOrder.status == "ESTIMATED"), "repairs_in_progress": count(ContainerRepairOrder, ContainerRepairOrder.status == "IN_PROGRESS"),
               "open_damages": count(ContainerDamage, ContainerDamage.status != "CLOSED"), "stock_counts_to_review": count(ContainerStockCount, ContainerStockCount.status == "OPEN")}
    return {"days": days, "csc": csc, "leases": leases, "pending": pending, "total": len(csc) + len(leases) + sum(pending.values())}
