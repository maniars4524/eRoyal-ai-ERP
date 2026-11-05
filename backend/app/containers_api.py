"""NVOCC container inventory: the container register (ISO 6346 numbers), status history, and IN / OUT movements with approval.

Ownership, grade, condition, availability, location and lease status are separate fields. A container's depot, yard location, location status
and availability change only through an APPROVED movement (or a condition change), and every change is written to an append-only history.
Everything lives in the user's own company. Reference lists (sizes, grades, conditions, ownership, depots, yard locations, movement types) come from Masters > Inventory.
"""
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number
from .db import get_db
from .models import Container, ContainerMovement, ContainerStatusHistory, MasterRecord, User

router = APIRouter(prefix="/api/containers", tags=["container inventory"])
movement_router = APIRouter(prefix="/api/container-movements", tags=["container movements"])

# ---------------------------------------------------------------- ISO 6346
_LETTER = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38]))
_ISO = re.compile(r"^[A-Z]{3}[UJZ][0-9]{7}$")


def normalise_number(value: str) -> str:
    return re.sub(r"[\s\-\.]", "", (value or "")).upper()


def check_digit(first10: str) -> int:
    total = sum((_LETTER[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(first10))
    return total % 11 % 10


def validate_number(value: str) -> str:
    """Return the clean container number or raise 422 saying what is wrong (format, category letter or check digit)."""
    n = normalise_number(value)
    if not _ISO.match(n):
        raise HTTPException(422, "Container number must be 3 owner letters + U, J or Z + 6 digits + 1 check digit (ISO 6346), for example MSKU 123456 0")
    if check_digit(n[:10]) != int(n[10]):
        raise HTTPException(422, f"The check digit is wrong: {n[:10]} must end with {check_digit(n[:10])} (ISO 6346)")
    return n


# ---------------------------------------------------------------- helpers
IN_PURPOSES = {"RECEIPT", "RETURN", "TRANSFER", "REPAIR_RETURN", "OTHER"}
OUT_PURPOSES = {"RELEASE", "TRANSFER", "REPAIR", "RETURN_LESSOR", "DISPOSAL", "OTHER"}
OUT_EFFECT = {"RELEASE": ("WITH_PARTY", "IN_USE"), "TRANSFER": ("IN_TRANSIT", "NOT_AVAILABLE"), "REPAIR": ("AT_REPAIR", "UNDER_REPAIR"), "RETURN_LESSOR": ("RETURNED", "NOT_AVAILABLE"),
              "DISPOSAL": ("DISPOSED", "NOT_AVAILABLE"), "OTHER": ("WITH_PARTY", "NOT_AVAILABLE")}


def _org(user: User) -> int:
    if not user.organization_id:
        raise HTTPException(403, "This function belongs to a company account")
    return user.organization_id


def _master(db: Session, org: int, mtype: str, *, name: str | None = None, code: str | None = None) -> MasterRecord | None:
    q = select(MasterRecord).where(MasterRecord.organization_id == org, MasterRecord.master_type == mtype, MasterRecord.status == "ACTIVE")
    if name is not None:
        q = q.where(func.lower(MasterRecord.name) == name.strip().lower())
    if code is not None:
        q = q.where(func.lower(MasterRecord.code) == code.strip().lower())
    return db.scalar(q)


def _require_master(db: Session, org: int, mtype: str, value: str | None, label: str) -> MasterRecord | None:
    if not value:
        return None
    rec = _master(db, org, mtype, name=value) or _master(db, org, mtype, code=value)
    if not rec:
        raise HTTPException(422, f"{label} \"{value}\" is not in Masters > Inventory. Add it there first.")
    return rec


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(c: Container, org_assets: dict[str, bool] | None = None) -> dict:
    return {"id": c.id, "container_number": c.container_number, "size": c.size, "container_type": c.container_type, "iso_code": c.iso_code, "manufacturer": c.manufacturer,
            "manufacturing_year": c.manufacturing_year, "manufacturing_country": c.manufacturing_country, "purchase_date": c.purchase_date, "hand": c.hand, "serial_number": c.serial_number,
            "csc_plate": c.csc_plate, "csc_valid_until": c.csc_valid_until, "description": c.description, "remarks": c.remarks, "ownership": c.ownership, "ownership_data": c.ownership_data or {},
            "grade": c.grade, "condition": c.condition, "availability": c.availability, "location_status": c.location_status, "lease_status": c.lease_status, "depot": c.depot,
            "depot_code": c.depot_code, "yard_location": c.yard_location, "is_active": c.is_active, "data": c.data or {}, "created_at": c.created_at, "updated_at": c.updated_at,
            "is_company_asset": (org_assets or {}).get((c.ownership or "").lower())}


def _history(db: Session, c: Container, field: str, old: Any, new: Any, user: User | None, reason: str | None = None, movement_id: int | None = None) -> None:
    if (old or None) == (new or None):
        return
    db.add(ContainerStatusHistory(organization_id=c.organization_id, container_id=c.id, field=field, old_value=None if old is None else str(old)[:200],
                                  new_value=None if new is None else str(new)[:200], reason=reason, movement_id=movement_id, user_id=user.id if user else None))


def _assets(db: Session, org: int) -> dict[str, bool]:
    """ownership name -> is it a company fixed asset (from the Ownership master's Treatment)."""
    rows = db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org, MasterRecord.master_type == "inv_container_ownership"))
    return {r.name.lower(): ((r.data or {}).get("treatment") == "Company fixed asset") for r in rows}


def _get(db: Session, org: int, container_id: int, lock: bool = False) -> Container:
    q = select(Container).where(Container.id == container_id, Container.organization_id == org)
    c = db.scalar(q.with_for_update() if lock else q)
    if not c:
        raise HTTPException(404, "Container not found")
    return c


class ContainerIn(BaseModel):
    container_number: str = Field(min_length=4, max_length=20)
    size: str | None = Field(default=None, max_length=60)
    container_type: str | None = Field(default=None, max_length=80)
    iso_code: str | None = Field(default=None, max_length=10)
    manufacturer: str | None = Field(default=None, max_length=120)
    manufacturing_year: int | None = Field(default=None, ge=1950, le=2100)
    manufacturing_country: str | None = Field(default=None, max_length=80)
    purchase_date: date | None = None
    hand: str | None = Field(default=None, pattern="^(First-hand|Second-hand)$")
    serial_number: str | None = Field(default=None, max_length=60)
    csc_plate: str | None = Field(default=None, max_length=120)
    csc_valid_until: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    remarks: str | None = Field(default=None, max_length=2000)
    ownership: str | None = Field(default=None, max_length=80)
    ownership_data: dict[str, Any] = Field(default_factory=dict)
    grade: str | None = Field(default=None, max_length=40)
    condition: str | None = Field(default=None, max_length=80)
    data: dict[str, Any] = Field(default_factory=dict)


class ContainerPatch(BaseModel):
    """Descriptive fields only. Ownership, grade, condition, location and availability have their own controlled endpoints."""
    size: str | None = Field(default=None, max_length=60)
    container_type: str | None = Field(default=None, max_length=80)
    iso_code: str | None = Field(default=None, max_length=10)
    manufacturer: str | None = Field(default=None, max_length=120)
    manufacturing_year: int | None = Field(default=None, ge=1950, le=2100)
    manufacturing_country: str | None = Field(default=None, max_length=80)
    purchase_date: date | None = None
    hand: str | None = Field(default=None, pattern="^(First-hand|Second-hand)$")
    serial_number: str | None = Field(default=None, max_length=60)
    csc_plate: str | None = Field(default=None, max_length=120)
    csc_valid_until: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    remarks: str | None = Field(default=None, max_length=2000)
    ownership_data: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class StatusChange(BaseModel):
    grade: str | None = Field(default=None, max_length=40)
    condition: str | None = Field(default=None, max_length=80)
    ownership: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------- container register
@router.get("/check-number")
def check_number(number: str = Query(max_length=30), user: User = Depends(permission_required("container.read"))):
    """ISO 6346 check for the form: the clean number, whether it is valid, and the check digit it should have."""
    n = normalise_number(number)
    if len(n) >= 10 and _ISO.match(n[:10] + "0"):
        d = check_digit(n[:10])
        return {"number": n, "valid": len(n) == 11 and n[10] == str(d), "expected_check_digit": d, "complete": n[:10] + str(d)}
    return {"number": n, "valid": False, "expected_check_digit": None, "complete": None}


@router.get("/summary")
def summary(user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    """Real counts for the depot / inventory dashboard: totals, by depot, ownership, size, condition and movements waiting for approval."""
    org = _org(user)
    live = [Container.organization_id == org, Container.is_active.is_(True)]
    total = db.scalar(select(func.count()).select_from(Container).where(*live)) or 0

    def grouped(col):
        return {k or "Not set": n for k, n in db.execute(select(col, func.count()).where(*live).group_by(col))}

    by_depot = []
    for depot, n, avail, use, rep, na in db.execute(select(Container.depot, func.count(),
                                                          func.sum(case((Container.availability == "AVAILABLE", 1), else_=0)), func.sum(case((Container.availability == "IN_USE", 1), else_=0)),
                                                          func.sum(case((Container.availability == "UNDER_REPAIR", 1), else_=0)), func.sum(case((Container.availability == "NOT_AVAILABLE", 1), else_=0)))
                                                   .where(*live, Container.location_status == "AT_DEPOT").group_by(Container.depot).order_by(Container.depot)):
        by_depot.append({"depot": depot, "total": n, "available": int(avail or 0), "in_use": int(use or 0), "under_repair": int(rep or 0), "not_available": int(na or 0)})
    pending = db.scalar(select(func.count()).select_from(ContainerMovement).where(ContainerMovement.organization_id == org, ContainerMovement.status == "DRAFT")) or 0
    return {"total": total, "by_depot": by_depot, "by_ownership": grouped(Container.ownership), "by_size": grouped(Container.size), "by_type": grouped(Container.container_type),
            "by_condition": grouped(Container.condition), "by_grade": grouped(Container.grade), "by_availability": grouped(Container.availability), "by_location_status": grouped(Container.location_status),
            "pending_movements": pending}


@router.get("")
def list_containers(q: str | None = Query(default=None, max_length=60), ownership: str | None = None, size: str | None = None, container_type: str | None = None, depot: str | None = None,
                    availability: str | None = None, condition: str | None = None, grade: str | None = None, location_status: str | None = None, include_inactive: bool = False,
                    limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    where = [Container.organization_id == org]
    if not include_inactive:
        where.append(Container.is_active.is_(True))
    for col, val in ((Container.ownership, ownership), (Container.size, size), (Container.container_type, container_type), (Container.depot, depot), (Container.availability, availability),
                     (Container.condition, condition), (Container.grade, grade), (Container.location_status, location_status)):
        if val:
            where.append(col == val)
    if q:
        like = f"%{normalise_number(q) or q.strip()}%"
        where.append(or_(Container.container_number.ilike(like), Container.serial_number.ilike(f"%{q.strip()}%"), Container.depot.ilike(f"%{q.strip()}%")))
    total = db.scalar(select(func.count()).select_from(Container).where(*where)) or 0
    rows = db.scalars(select(Container).where(*where).order_by(Container.container_number).limit(limit).offset(offset)).all()
    assets = _assets(db, org)
    return {"items": [_out(c, assets) for c in rows], "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=201)
def create_container(body: ContainerIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    number = validate_number(body.container_number)
    if db.scalar(select(Container.id).where(Container.organization_id == org, Container.container_number == number)):
        raise HTTPException(409, f"Container {number} is already registered")
    _require_master(db, org, "inv_container_size", body.size, "Container size")
    if body.container_type:
        _require_master(db, org, "container_type", body.container_type, "Container type")
    own = _require_master(db, org, "inv_container_ownership", body.ownership, "Ownership")
    _require_master(db, org, "inv_container_grade", body.grade, "Grade")
    _require_master(db, org, "inv_container_condition", body.condition, "Condition")
    fields = body.model_dump(exclude={"container_number"})
    c = Container(organization_id=org, container_number=number, created_by=user.id, **fields)
    c.ownership = own.name if own else body.ownership
    db.add(c)
    db.flush()
    for f in ("ownership", "grade", "condition"):
        _history(db, c, f, None, getattr(c, f), user, "Registered")
    record_audit(db, "CONTAINER_CREATED", "INVENTORY", user, org, "Container", str(c.id), {"container_number": number})
    db.commit()
    return _out(c, _assets(db, org))


@router.get("/{container_id}")
def get_container(container_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, container_id)
    hist = db.scalars(select(ContainerStatusHistory).where(ContainerStatusHistory.container_id == c.id).order_by(ContainerStatusHistory.id.desc()).limit(200)).all()
    moves = db.scalars(select(ContainerMovement).where(ContainerMovement.container_id == c.id).order_by(ContainerMovement.id.desc()).limit(100)).all()
    names = {u.id: (u.first_name + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.id.in_({h.user_id for h in hist if h.user_id})))} if hist else {}
    return {**_out(c, _assets(db, org)),
            "history": [{"id": h.id, "field": h.field, "old": h.old_value, "new": h.new_value, "reason": h.reason, "movement_id": h.movement_id, "by": names.get(h.user_id), "at": h.created_at} for h in hist],
            "movements": [_move_out(m) for m in moves]}


@router.patch("/{container_id}")
def update_container(container_id: int, body: ContainerPatch, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, container_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "size" in changes:
        _require_master(db, org, "inv_container_size", changes["size"], "Container size")
    if changes.get("container_type"):
        _require_master(db, org, "container_type", changes["container_type"], "Container type")
    old = {k: getattr(c, k) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v)
    record_audit(db, "CONTAINER_UPDATED", "INVENTORY", user, org, "Container", str(c.id), {k: str(v) for k, v in changes.items()}, old_value={k: str(v) for k, v in old.items()})
    db.commit()
    return _out(c, _assets(db, org))


@router.post("/{container_id}/status")
def change_status(container_id: int, body: StatusChange, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    """Change condition (anyone who can edit containers) or grade / ownership (approvers only). The old value is kept in the history with the reason."""
    org = _org(user)
    c = _get(db, org, container_id, lock=True)
    if (body.grade or body.ownership) and not has_permission(user, "container.approve"):
        raise HTTPException(403, "Changing grade or ownership needs approval rights")
    if not (body.grade or body.condition or body.ownership):
        raise HTTPException(422, "Nothing to change")
    if c.location_status == "DISPOSED":
        raise HTTPException(409, "A disposed container cannot be changed")
    if body.grade:
        _require_master(db, org, "inv_container_grade", body.grade, "Grade")
        _history(db, c, "grade", c.grade, body.grade, user, body.reason)
        c.grade = body.grade
    if body.ownership:
        rec = _require_master(db, org, "inv_container_ownership", body.ownership, "Ownership")
        _history(db, c, "ownership", c.ownership, rec.name, user, body.reason)
        c.ownership = rec.name
    if body.condition:
        rec = _require_master(db, org, "inv_container_condition", body.condition, "Condition")
        _history(db, c, "condition", c.condition, rec.name, user, body.reason)
        c.condition = rec.name
        usable = (rec.data or {}).get("available_for_use") != "No"
        if c.location_status == "AT_DEPOT":
            if not usable and c.availability == "AVAILABLE":
                _history(db, c, "availability", c.availability, "NOT_AVAILABLE", user, f"Condition {rec.name} is not available for use")
                c.availability = "NOT_AVAILABLE"
            elif usable and c.availability == "NOT_AVAILABLE":
                _history(db, c, "availability", c.availability, "AVAILABLE", user, f"Condition {rec.name} is available for use")
                c.availability = "AVAILABLE"
    record_audit(db, "CONTAINER_STATUS_CHANGED", "INVENTORY", user, org, "Container", str(c.id), {"grade": body.grade, "condition": body.condition, "ownership": body.ownership, "reason": body.reason})
    db.commit()
    return _out(c, _assets(db, org))


# ---------------------------------------------------------------- movements
class MovementIn(BaseModel):
    container_id: int
    direction: str = Field(pattern="^(IN|OUT)$")
    purpose: str = Field(max_length=20)
    movement_type: str | None = Field(default=None, max_length=120)
    from_depot: str | None = Field(default=None, max_length=120)
    to_depot: str | None = Field(default=None, max_length=120)
    to_location: str | None = Field(default=None, max_length=60)
    movement_date: date
    movement_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    truck_number: str | None = Field(default=None, max_length=30)
    driver_name: str | None = Field(default=None, max_length=80)
    transport_company: str | None = Field(default=None, max_length=120)
    customer: str | None = Field(default=None, max_length=160)
    agent: str | None = Field(default=None, max_length=160)
    booking_number: str | None = Field(default=None, max_length=40)
    bl_number: str | None = Field(default=None, max_length=40)
    job_reference: str | None = Field(default=None, max_length=40)
    eir_number: str | None = Field(default=None, max_length=40)
    reason: str | None = Field(default=None, max_length=500)
    condition_departure: str | None = Field(default=None, max_length=80)
    condition_arrival: str | None = Field(default=None, max_length=80)
    damage_remarks: str | None = Field(default=None, max_length=1000)


class Decision(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _move_out(m: ContainerMovement, number: str | None = None) -> dict:
    return {"id": m.id, "number": m.number, "container_id": m.container_id, "container_number": number, "direction": m.direction, "purpose": m.purpose, "movement_type": m.movement_type,
            "from_depot": m.from_depot, "to_depot": m.to_depot, "from_location": m.from_location, "to_location": m.to_location, "movement_date": m.movement_date, "movement_time": m.movement_time,
            "truck_number": m.truck_number, "driver_name": m.driver_name, "transport_company": m.transport_company, "customer": m.customer, "agent": m.agent, "booking_number": m.booking_number,
            "bl_number": m.bl_number, "job_reference": m.job_reference, "eir_number": m.eir_number, "reason": m.reason, "condition_departure": m.condition_departure,
            "condition_arrival": m.condition_arrival, "damage_remarks": m.damage_remarks, "status": m.status, "created_by": m.created_by, "approved_by": m.approved_by,
            "approved_at": m.approved_at, "reversed_at": m.reversed_at, "reversal_reason": m.reversal_reason, "created_at": m.created_at}


def _check_state(c: Container, m: ContainerMovement) -> None:
    """The container must be in a state that allows this movement. Run when the draft is made and again when it is approved."""
    if not c.is_active:
        raise HTTPException(409, f"Container {c.container_number} is not active")
    if c.location_status == "DISPOSED":
        raise HTTPException(409, f"Container {c.container_number} was disposed of and cannot be moved again")
    if m.direction == "IN":
        if c.location_status == "AT_DEPOT":
            raise HTTPException(409, f"Container {c.container_number} is already at depot {c.depot}: it must go OUT before it can come IN again")
    else:
        if c.location_status != "AT_DEPOT" or (c.depot or "").lower() != (m.from_depot or "").lower():
            raise HTTPException(409, f"Container {c.container_number} is not at depot {m.from_depot} (it is {c.location_status.replace('_', ' ').lower()}" + (f" at {c.depot}" if c.depot else "") + ")")
        if m.purpose == "RELEASE" and c.availability != "AVAILABLE":
            raise HTTPException(409, f"Container {c.container_number} is {c.availability.replace('_', ' ').lower()} and cannot be released for use")


@movement_router.get("")
def list_movements(q: str | None = Query(default=None, max_length=60), direction: str | None = Query(default=None, pattern="^(IN|OUT)$"), status: str | None = Query(default=None, pattern="^(DRAFT|APPROVED|REVERSED|CANCELLED)$"),
                   depot: str | None = None, container_id: int | None = None, date_from: date | None = None, date_to: date | None = None, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
                   user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    where = [ContainerMovement.organization_id == org]
    if direction:
        where.append(ContainerMovement.direction == direction)
    if status:
        where.append(ContainerMovement.status == status)
    if depot:
        where.append(or_(ContainerMovement.from_depot == depot, ContainerMovement.to_depot == depot))
    if container_id:
        where.append(ContainerMovement.container_id == container_id)
    if date_from:
        where.append(ContainerMovement.movement_date >= date_from)
    if date_to:
        where.append(ContainerMovement.movement_date <= date_to)
    base = select(ContainerMovement, Container.container_number).join(Container, Container.id == ContainerMovement.container_id).where(*where)
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Container.container_number.ilike(f"%{normalise_number(q) or q.strip()}%"), ContainerMovement.number.ilike(like), ContainerMovement.booking_number.ilike(like),
                              ContainerMovement.bl_number.ilike(like), ContainerMovement.truck_number.ilike(like), ContainerMovement.eir_number.ilike(like)))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(base.order_by(ContainerMovement.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [_move_out(m, n) for m, n in rows], "total": total, "limit": limit, "offset": offset}


@movement_router.post("", status_code=201)
def create_movement(body: MovementIn, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    c = _get(db, org, body.container_id, lock=True)
    allowed = IN_PURPOSES if body.direction == "IN" else OUT_PURPOSES
    if body.purpose not in allowed:
        raise HTTPException(422, f"For a movement {body.direction} the purpose must be one of: {', '.join(sorted(allowed))}")
    m = ContainerMovement(organization_id=org, container_id=c.id, direction=body.direction, purpose=body.purpose, created_by=user.id,
                          **body.model_dump(exclude={"container_id", "direction", "purpose", "to_depot", "from_depot", "to_location"}))
    if body.movement_type:
        mt = _require_master(db, org, "inv_movement_type", body.movement_type, "Movement type")
        if (mt.data or {}).get("direction") not in (None, "", body.direction):
            raise HTTPException(422, f"Movement type {mt.name} is a {(mt.data or {}).get('direction')} movement, not {body.direction}")
        m.movement_type = mt.name
    if body.direction == "IN":
        if not body.to_depot:
            raise HTTPException(422, "Choose the depot the container is coming into")
        dep = _require_master(db, org, "inv_depot", body.to_depot, "Depot")
        m.to_depot, m.from_depot = dep.name, body.from_depot
        if body.to_location:
            loc = _require_master(db, org, "inv_yard_location", body.to_location, "Yard location")
            if (loc.data or {}).get("depot", "").lower() != dep.name.lower():
                raise HTTPException(422, f"Yard location {loc.code} belongs to depot {(loc.data or {}).get('depot') or '—'}, not {dep.name}")
            m.to_location = loc.code
    else:
        m.from_depot = body.from_depot or c.depot
        m.from_location = c.yard_location
        if body.to_depot:
            _require_master(db, org, "inv_depot", body.to_depot, "Destination depot")
            m.to_depot = body.to_depot
    _check_state(c, m)
    if db.scalar(select(ContainerMovement.id).where(ContainerMovement.organization_id == org, ContainerMovement.container_id == c.id, ContainerMovement.direction == body.direction,
                                                    ContainerMovement.movement_date == body.movement_date, ContainerMovement.movement_time == body.movement_time,
                                                    ContainerMovement.status.in_(["DRAFT", "APPROVED"]))):
        raise HTTPException(409, "The same movement for this container, date and time already exists")
    open_draft = db.scalar(select(ContainerMovement.number).where(ContainerMovement.organization_id == org, ContainerMovement.container_id == c.id, ContainerMovement.status == "DRAFT"))
    if open_draft:
        raise HTTPException(409, f"Container {c.container_number} already has a movement waiting for approval ({open_draft}). Approve or cancel it first")
    m.number = next_number(db, org, "container_movement", "CMV", body.movement_date, user=user)
    db.add(m)
    db.flush()
    record_audit(db, "CONTAINER_MOVEMENT_CREATED", "INVENTORY", user, org, "ContainerMovement", str(m.id), {"number": m.number, "container": c.container_number, "direction": m.direction, "purpose": m.purpose})
    db.commit()
    return _move_out(m, c.container_number)


def _movement(db: Session, org: int, movement_id: int, lock: bool = False) -> ContainerMovement:
    q = select(ContainerMovement).where(ContainerMovement.id == movement_id, ContainerMovement.organization_id == org)
    m = db.scalar(q.with_for_update() if lock else q)
    if not m:
        raise HTTPException(404, "Movement not found")
    return m


@movement_router.get("/{movement_id}")
def get_movement(movement_id: int, user: User = Depends(permission_required("container.read")), db: Session = Depends(get_db)):
    org = _org(user)
    m = _movement(db, org, movement_id)
    return _move_out(m, db.scalar(select(Container.container_number).where(Container.id == m.container_id)))


def _sod_blocked(db: Session, user: User, m: ContainerMovement) -> bool:
    """Approving your own movement is refused while another active user can approve it; a one-person company must still be able to work."""
    if m.created_by != user.id:
        return False
    others = [u for u in db.scalars(select(User).where(User.organization_id == m.organization_id, User.status == "ACTIVE", User.id != user.id))
              if u.is_super_admin or any(p.code == "container.approve" for r in u.roles for p in r.permissions)]
    return bool(others)


@movement_router.post("/{movement_id}/approve")
def approve_movement(movement_id: int, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    m = _movement(db, org, movement_id, lock=True)
    if m.status != "DRAFT":
        raise HTTPException(409, f"This movement is already {m.status.lower()}")
    if _sod_blocked(db, user, m):
        raise HTTPException(403, "You created this movement; another approver must approve it")
    c = _get(db, org, m.container_id, lock=True)
    _check_state(c, m)
    m.before_state = {"depot": c.depot, "depot_code": c.depot_code, "yard_location": c.yard_location, "location_status": c.location_status, "availability": c.availability, "condition": c.condition}
    old = dict(m.before_state)
    if m.direction == "IN":
        dep = _master(db, org, "inv_depot", name=m.to_depot)
        cond = _master(db, org, "inv_container_condition", name=m.condition_arrival or c.condition or "") if (m.condition_arrival or c.condition) else None
        if m.condition_arrival and cond and cond.name != c.condition:
            _history(db, c, "condition", c.condition, cond.name, user, f"Condition on arrival ({m.number})", m.id)
            c.condition = cond.name
        c.depot, c.depot_code, c.yard_location, c.location_status = m.to_depot, dep.code if dep else None, m.to_location, "AT_DEPOT"
        c.availability = "NOT_AVAILABLE" if (cond and (cond.data or {}).get("available_for_use") == "No") else "AVAILABLE"
    else:
        status, avail = OUT_EFFECT[m.purpose]
        c.depot = c.depot_code = c.yard_location = None
        c.location_status, c.availability = status, avail
    for field, key in (("depot", "depot"), ("yard_location", "yard_location"), ("location_status", "location_status"), ("availability", "availability")):
        _history(db, c, field, old[key], getattr(c, key), user, f"{m.direction} {m.purpose.replace('_', ' ').lower()} ({m.number})", m.id)
    m.status, m.approved_by, m.approved_at = "APPROVED", user.id, _now()
    record_audit(db, "CONTAINER_MOVEMENT_APPROVED", "INVENTORY", user, org, "ContainerMovement", str(m.id), {"number": m.number, "container": c.container_number})
    db.commit()
    return _move_out(m, c.container_number)


@movement_router.post("/{movement_id}/cancel")
def cancel_movement(movement_id: int, body: Decision, user: User = Depends(permission_required("container.create")), db: Session = Depends(get_db)):
    org = _org(user)
    m = _movement(db, org, movement_id, lock=True)
    if m.status != "DRAFT":
        raise HTTPException(409, "Only a movement waiting for approval can be cancelled; use Reverse for an approved one")
    if m.created_by != user.id and not has_permission(user, "container.approve"):
        raise HTTPException(403, "Only the person who made it, or an approver, can cancel this movement")
    m.status, m.reversal_reason = "CANCELLED", body.reason
    record_audit(db, "CONTAINER_MOVEMENT_CANCELLED", "INVENTORY", user, org, "ContainerMovement", str(m.id), {"number": m.number, "reason": body.reason})
    db.commit()
    return _move_out(m)


@movement_router.post("/{movement_id}/reverse")
def reverse_movement(movement_id: int, body: Decision, user: User = Depends(permission_required("container.approve")), db: Session = Depends(get_db)):
    """Undo an approved movement: the container goes back exactly as it was, and both steps stay in the history."""
    org = _org(user)
    m = _movement(db, org, movement_id, lock=True)
    if m.status != "APPROVED":
        raise HTTPException(409, "Only an approved movement can be reversed")
    later = db.scalar(select(ContainerMovement.number).where(ContainerMovement.container_id == m.container_id, ContainerMovement.id > m.id, ContainerMovement.status == "APPROVED"))
    if later:
        raise HTTPException(409, f"A later movement ({later}) exists for this container. Reverse that one first")
    c = _get(db, org, m.container_id, lock=True)
    before = m.before_state or {}
    cur = {"depot": c.depot, "yard_location": c.yard_location, "location_status": c.location_status, "availability": c.availability, "condition": c.condition}
    for k in ("depot", "depot_code", "yard_location", "location_status", "availability", "condition"):
        if k in before:
            setattr(c, k, before[k])
    for field in ("depot", "yard_location", "location_status", "availability", "condition"):
        _history(db, c, field, cur[field], getattr(c, field), user, f"Reversal of {m.number}: {body.reason}", m.id)
    m.status, m.reversed_by, m.reversed_at, m.reversal_reason = "REVERSED", user.id, _now(), body.reason
    record_audit(db, "CONTAINER_MOVEMENT_REVERSED", "INVENTORY", user, org, "ContainerMovement", str(m.id), {"number": m.number, "reason": body.reason})
    db.commit()
    return _move_out(m, c.container_number)
