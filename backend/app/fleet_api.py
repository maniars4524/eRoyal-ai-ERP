"""Transport fleet: vehicle register, status and allocation rules, compliance documents, workshop work orders, preventive maintenance plans, tyre lifecycle by wheel position, fuel.

A vehicle's status changes only through controlled actions (allocate / release, workshop work orders, status change) and each change is written to its history.
A vehicle cannot be allocated while a mandatory document is expired or missing, or while it is in the workshop. A tyre can be fitted to one vehicle position at a time,
and only to a position that exists in the vehicle's Axle & Wheel Configuration master. Reference lists come from Masters > Inventory.
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number
from .container_ops_api import _m, _page
from .containers_api import _master, _now, _org, _require_master
from .db import get_db
from .models import FuelTransaction, Tyre, TyreEvent, User, Vehicle, VehicleDocument, VehicleHistory, VehiclePlan, VehicleWorkOrder

vehicle_router = APIRouter(prefix="/api/vehicles", tags=["fleet vehicles"])
wo_router = APIRouter(prefix="/api/vehicle-work-orders", tags=["vehicle maintenance"])
plan_router = APIRouter(prefix="/api/vehicle-plans", tags=["vehicle maintenance plans"])
tyre_router = APIRouter(prefix="/api/tyres", tags=["tyres"])
fuel_router = APIRouter(prefix="/api/fuel", tags=["fuel"])
alert_router = APIRouter(prefix="/api/fleet-alerts", tags=["fleet alerts"])

Money = Decimal
OPEN_WO = ("OPEN", "IN_PROGRESS", "COMPLETED")
MANUAL_STATUS = {"AVAILABLE", "IDLE", "BREAKDOWN", "ACCIDENT", "SOLD", "SCRAPPED"}
DISPOSED = {"SOLD", "SCRAPPED"}


# ---------------------------------------------------------------- helpers
def _reg(value: str) -> str:
    return re.sub(r"[\s\-\.]", "", value or "").upper()


def _today() -> date:
    return date.today()


def _vehicle(db: Session, org: int, vid: int, lock: bool = False) -> Vehicle:
    q = select(Vehicle).where(Vehicle.id == vid, Vehicle.organization_id == org)
    v = db.scalar(q.with_for_update() if lock else q)
    if not v:
        raise HTTPException(404, "Vehicle not found")
    return v


def _hist(db: Session, v: Vehicle, field: str, old: Any, new: Any, user: User | None, reason: str | None = None) -> None:
    if (old or None) == (new or None):
        return
    db.add(VehicleHistory(organization_id=v.organization_id, vehicle_id=v.id, field=field, old_value=None if old is None else str(old)[:200], new_value=None if new is None else str(new)[:200],
                          reason=reason, user_id=user.id if user else None))


def _set_status(db: Session, v: Vehicle, new: str, user: User | None, reason: str) -> None:
    if v.status != new:
        _hist(db, v, "status", v.status, new, user, reason)
        v.status = new


def _bump_odometer(db: Session, v: Vehicle, reading: int | None, user: User, reason: str) -> None:
    """The odometer only moves forward: a lower reading is refused, a higher one is recorded."""
    if reading is None:
        return
    if reading < v.odometer:
        raise HTTPException(409, f"Odometer reading {reading} is lower than the vehicle's recorded {v.odometer}")
    if reading > v.odometer:
        _hist(db, v, "odometer", v.odometer, reading, user, reason)
        v.odometer = reading


def _sod_blocked(db: Session, user: User, org: int, created_by: int | None) -> bool:
    if created_by != user.id:
        return False
    return bool([u for u in db.scalars(select(User).where(User.organization_id == org, User.status == "ACTIVE", User.id != user.id))
                 if u.is_super_admin or any(p.code == "fleet.approve" for r in u.roles for p in r.permissions)])


def _positions(db: Session, org: int, axle_config: str | None) -> list[str]:
    """Wheel positions come from the Axle & Wheel Configuration master (its Position Labels, one per line or comma separated), never from a fixed list."""
    if not axle_config:
        return []
    rec = _master(db, org, "inv_axle_config", name=axle_config) or _master(db, org, "inv_axle_config", code=axle_config)
    raw = (rec.data or {}).get("position_labels", "") if rec else ""
    seen, out = set(), []
    for p in re.split(r"[\n,;]+", raw):
        p = p.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _doc_out(d: VehicleDocument) -> dict:
    today = _today()
    return {"id": d.id, "vehicle_id": d.vehicle_id, "doc_type": d.doc_type, "number": d.number, "issue_date": d.issue_date, "expiry_date": d.expiry_date, "authority": d.authority, "mandatory": d.mandatory,
            "status": d.status, "expired": bool(d.expiry_date and d.expiry_date < today), "days_left": (d.expiry_date - today).days if d.expiry_date else None, "remarks": d.remarks, "created_at": d.created_at}


def _allocation_issues(db: Session, v: Vehicle) -> tuple[list[str], list[str]]:
    """(reasons that block allocation, warnings). Expired or missing mandatory documents block; an overdue service only warns."""
    block, warn = [], []
    if v.status not in ("AVAILABLE", "IDLE"):
        block.append(f"The vehicle is {v.status.replace('_', ' ').lower()}")
    today = _today()
    current = {d.doc_type.lower(): d for d in db.scalars(select(VehicleDocument).where(VehicleDocument.vehicle_id == v.id, VehicleDocument.status == "CURRENT"))}
    for d in current.values():
        if d.mandatory and d.expiry_date and d.expiry_date < today:
            block.append(f"{d.doc_type} expired on {d.expiry_date.isoformat()}")
    from .models import MasterRecord
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == v.organization_id, MasterRecord.master_type == "inv_document_type", MasterRecord.status == "ACTIVE")):
        data = r.data or {}
        if data.get("mandatory") == "Yes" and data.get("applies_to") in ("Vehicle", "Both") and r.name.lower() not in current:
            block.append(f"No {r.name} on file")
    for p in db.scalars(select(VehiclePlan).where(VehiclePlan.vehicle_id == v.id, VehiclePlan.is_active.is_(True))):
        due = _plan_due(p, v)
        if due["overdue"]:
            warn.append(f"Service overdue: {p.name}")
    return block, warn


def _plan_due(p: VehiclePlan, v: Vehicle) -> dict:
    today = _today()
    km_left = (p.last_done_odometer + p.interval_km - v.odometer) if (p.interval_km and p.last_done_odometer is not None) else None
    days_left = (p.last_done_date + timedelta(days=p.interval_days) - today).days if (p.interval_days and p.last_done_date) else None
    overdue = (km_left is not None and km_left <= 0) or (days_left is not None and days_left <= 0)
    soon = (km_left is not None and km_left <= 500) or (days_left is not None and days_left <= 15)
    return {"km_left": km_left, "days_left": days_left, "overdue": overdue, "due_soon": soon and not overdue}


# ---------------------------------------------------------------- vehicles
class VehicleIn(BaseModel):
    registration_number: str = Field(min_length=4, max_length=20)
    category: str | None = Field(default=None, max_length=80)
    vehicle_type: str | None = Field(default=None, max_length=80)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    manufacturing_year: int | None = Field(default=None, ge=1950, le=2100)
    hand: str | None = Field(default=None, pattern="^(First-hand|Second-hand)$")
    chassis_number: str | None = Field(default=None, max_length=40)
    engine_number: str | None = Field(default=None, max_length=40)
    vin: str | None = Field(default=None, max_length=40)
    fuel_type: str | None = Field(default=None, max_length=20)
    engine_capacity: str | None = Field(default=None, max_length=20)
    tonnage: Money | None = Field(default=None, ge=0)
    payload_capacity: Money | None = Field(default=None, ge=0)
    axle_config: str | None = Field(default=None, max_length=80)
    purchase_date: date | None = None
    purchase_cost: Money | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=160)
    purchase_invoice: str | None = Field(default=None, max_length=60)
    warranty_until: date | None = None
    odometer: int = Field(default=0, ge=0)
    location: str | None = Field(default=None, max_length=120)
    driver: str | None = Field(default=None, max_length=120)
    branch: str | None = Field(default=None, max_length=80)
    ownership: str | None = Field(default=None, max_length=80)
    ownership_data: dict[str, Any] = Field(default_factory=dict)
    remarks: str | None = Field(default=None, max_length=2000)


class VehiclePatch(BaseModel):
    vehicle_type: str | None = Field(default=None, max_length=80)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    manufacturing_year: int | None = Field(default=None, ge=1950, le=2100)
    engine_number: str | None = Field(default=None, max_length=40)
    vin: str | None = Field(default=None, max_length=40)
    fuel_type: str | None = Field(default=None, max_length=20)
    engine_capacity: str | None = Field(default=None, max_length=20)
    tonnage: Money | None = Field(default=None, ge=0)
    payload_capacity: Money | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=160)
    purchase_invoice: str | None = Field(default=None, max_length=60)
    warranty_until: date | None = None
    location: str | None = Field(default=None, max_length=120)
    driver: str | None = Field(default=None, max_length=120)
    branch: str | None = Field(default=None, max_length=80)
    ownership_data: dict[str, Any] | None = None
    remarks: str | None = Field(default=None, max_length=2000)


class StatusIn(BaseModel):
    status: str = Field(max_length=20)
    reason: str = Field(min_length=3, max_length=500)


class AllocateIn(BaseModel):
    driver: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="Allocated to a trip", max_length=500)
    odometer: int | None = Field(default=None, ge=0)


class OdoIn(BaseModel):
    odometer: int = Field(ge=0)
    reason: str = Field(default="Reading recorded", max_length=300)


def _veh_out(v: Vehicle) -> dict:
    return {"id": v.id, "asset_id": v.asset_id, "registration_number": v.registration_number, "category": v.category, "vehicle_type": v.vehicle_type, "make": v.make, "model": v.model,
            "manufacturing_year": v.manufacturing_year, "hand": v.hand, "chassis_number": v.chassis_number, "engine_number": v.engine_number, "vin": v.vin, "fuel_type": v.fuel_type,
            "engine_capacity": v.engine_capacity, "tonnage": _m(v.tonnage), "payload_capacity": _m(v.payload_capacity), "axle_config": v.axle_config, "purchase_date": v.purchase_date,
            "purchase_cost": _m(v.purchase_cost), "supplier": v.supplier, "purchase_invoice": v.purchase_invoice, "warranty_until": v.warranty_until, "odometer": v.odometer, "location": v.location,
            "driver": v.driver, "branch": v.branch, "ownership": v.ownership, "ownership_data": v.ownership_data or {}, "status": v.status, "remarks": v.remarks, "is_active": v.is_active, "created_at": v.created_at}


@vehicle_router.get("/summary")
def summary(user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    """Real figures for the fleet dashboard."""
    org = _org(user)
    live = [Vehicle.organization_id == org, Vehicle.is_active.is_(True)]

    def grouped(col):
        return {k or "Not set": n for k, n in db.execute(select(col, func.count()).where(*live).group_by(col))}

    today = _today()
    docs = db.scalars(select(VehicleDocument).join(Vehicle, Vehicle.id == VehicleDocument.vehicle_id).where(VehicleDocument.organization_id == org, VehicleDocument.status == "CURRENT", Vehicle.is_active.is_(True),
                                                                                                         Vehicle.status.notin_(list(DISPOSED)), VehicleDocument.expiry_date.is_not(None))).all()
    plans_due = 0
    for p, v in db.execute(select(VehiclePlan, Vehicle).join(Vehicle, Vehicle.id == VehiclePlan.vehicle_id).where(VehiclePlan.organization_id == org, VehiclePlan.is_active.is_(True), Vehicle.is_active.is_(True))):
        if _plan_due(p, v)["overdue"]:
            plans_due += 1
    wo = db.scalars(select(VehicleWorkOrder).where(VehicleWorkOrder.organization_id == org)).all()
    closed = [w for w in wo if w.status == "CLOSED"]
    downs = [w.downtime_days for w in closed if w.downtime_days is not None]
    fuel = db.execute(select(func.coalesce(func.sum(FuelTransaction.amount), 0), func.coalesce(func.sum(FuelTransaction.litres), 0)).where(FuelTransaction.organization_id == org, FuelTransaction.kind == "FUEL")).one()
    tyre_status = {k: n for k, n in db.execute(select(Tyre.status, func.count()).where(Tyre.organization_id == org).group_by(Tyre.status))}
    tyre_cost = db.scalar(select(func.coalesce(func.sum(Tyre.purchase_cost + Tyre.extra_cost), 0)).where(Tyre.organization_id == org)) or 0
    return {"total": sum(grouped(Vehicle.status).values()), "by_status": grouped(Vehicle.status), "by_ownership": grouped(Vehicle.ownership), "by_category": grouped(Vehicle.category),
            "documents_expired": sum(1 for d in docs if d.expiry_date < today), "documents_due_60_days": sum(1 for d in docs if today <= d.expiry_date <= today + timedelta(days=60)),
            "maintenance_overdue": plans_due, "open_work_orders": sum(1 for w in wo if w.status in OPEN_WO), "average_downtime_days": round(sum(downs) / len(downs), 1) if downs else None,
            "maintenance_cost": str(sum((w.total_cost for w in closed), Decimal(0))), "fuel_cost": str(fuel[0]), "fuel_litres": str(fuel[1]), "tyres_by_status": tyre_status, "tyre_cost": str(tyre_cost)}


@vehicle_router.get("")
def list_vehicles(q: str | None = Query(default=None, max_length=40), category: str | None = None, ownership: str | None = None, status: str | None = None, branch: str | None = None,
                  include_disposed: bool = False, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [Vehicle.organization_id == org, Vehicle.is_active.is_(True)]
    if not include_disposed and not status:
        w.append(Vehicle.status.notin_(list(DISPOSED)))
    for col, val in ((Vehicle.category, category), (Vehicle.ownership, ownership), (Vehicle.status, status), (Vehicle.branch, branch)):
        if val:
            w.append(col == val)
    if q:
        like = f"%{q.strip()}%"
        w.append(or_(Vehicle.registration_number.ilike(f"%{_reg(q) or q}%"), Vehicle.asset_id.ilike(like), Vehicle.chassis_number.ilike(like), Vehicle.driver.ilike(like), Vehicle.make.ilike(like)))
    total, rows = _page(select(Vehicle).where(*w), db, limit, offset, Vehicle.registration_number)
    return {"items": [_veh_out(v) for v in rows], "total": total}


@vehicle_router.post("", status_code=201)
def create_vehicle(body: VehicleIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    reg = _reg(body.registration_number)
    if not re.match(r"^[A-Z0-9]{4,15}$", reg):
        raise HTTPException(422, "Registration number may contain only letters and digits")
    if db.scalar(select(Vehicle.id).where(Vehicle.organization_id == org, Vehicle.registration_number == reg)):
        raise HTTPException(409, f"Vehicle {reg} is already registered")
    if body.chassis_number and db.scalar(select(Vehicle.id).where(Vehicle.organization_id == org, func.upper(Vehicle.chassis_number) == body.chassis_number.strip().upper())):
        raise HTTPException(409, f"Chassis number {body.chassis_number} is already registered to another vehicle")
    if body.category:
        _require_master(db, org, "inv_vehicle_category", body.category, "Vehicle category")
    if body.axle_config:
        _require_master(db, org, "inv_axle_config", body.axle_config, "Axle & wheel configuration")
    own = _require_master(db, org, "inv_vehicle_ownership", body.ownership, "Vehicle ownership") if body.ownership else None
    v = Vehicle(organization_id=org, created_by=user.id, **{**body.model_dump(), "registration_number": reg})
    v.ownership = own.name if own else body.ownership
    v.asset_id = next_number(db, org, "vehicle", "VEH", _today(), user=user)
    db.add(v)
    db.flush()
    _hist(db, v, "status", None, "AVAILABLE", user, "Registered")
    record_audit(db, "VEHICLE_CREATED", "FLEET", user, org, "Vehicle", str(v.id), {"registration": reg, "asset_id": v.asset_id})
    db.commit()
    return _veh_out(v)


@vehicle_router.get("/{vehicle_id}")
def get_vehicle(vehicle_id: int, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, vehicle_id)
    hist = db.scalars(select(VehicleHistory).where(VehicleHistory.vehicle_id == v.id).order_by(VehicleHistory.id.desc()).limit(200)).all()
    names = {u.id: (u.first_name + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.id.in_({h.user_id for h in hist if h.user_id})))} if hist else {}
    docs = db.scalars(select(VehicleDocument).where(VehicleDocument.vehicle_id == v.id).order_by(VehicleDocument.id.desc())).all()
    wos = db.scalars(select(VehicleWorkOrder).where(VehicleWorkOrder.vehicle_id == v.id).order_by(VehicleWorkOrder.id.desc()).limit(50)).all()
    plans = db.scalars(select(VehiclePlan).where(VehiclePlan.vehicle_id == v.id).order_by(VehiclePlan.id)).all()
    block, warn = _allocation_issues(db, v)
    fuel = db.scalars(select(FuelTransaction).where(FuelTransaction.vehicle_id == v.id).order_by(FuelTransaction.id.desc()).limit(20)).all()
    maint_cost = db.scalar(select(func.coalesce(func.sum(VehicleWorkOrder.total_cost), 0)).where(VehicleWorkOrder.vehicle_id == v.id, VehicleWorkOrder.status == "CLOSED")) or 0
    fuel_cost = db.scalar(select(func.coalesce(func.sum(FuelTransaction.amount), 0)).where(FuelTransaction.vehicle_id == v.id)) or 0
    return {**_veh_out(v), "history": [{"field": h.field, "old": h.old_value, "new": h.new_value, "reason": h.reason, "by": names.get(h.user_id), "at": h.created_at} for h in hist],
            "documents": [_doc_out(d) for d in docs], "work_orders": [_wo_out(w, v.registration_number) for w in wos], "plans": [_plan_out(p, v) for p in plans],
            "allocation": {"allowed": not block, "blocked_because": block, "warnings": warn}, "fuel": [_fuel_out(f, v.registration_number) for f in fuel],
            "costs": {"maintenance": str(maint_cost), "fuel": str(fuel_cost)}}


@vehicle_router.patch("/{vehicle_id}")
def update_vehicle(vehicle_id: int, body: VehiclePatch, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if v.status in DISPOSED:
        raise HTTPException(409, "A sold or scrapped vehicle cannot be edited")
    for k, val in changes.items():
        if k in ("driver", "location", "branch"):
            _hist(db, v, k, getattr(v, k), val, user)
        setattr(v, k, val)
    record_audit(db, "VEHICLE_UPDATED", "FLEET", user, org, "Vehicle", str(v.id), {k: str(x) for k, x in changes.items()})
    db.commit()
    return _veh_out(v)


@vehicle_router.get("/{vehicle_id}/allocation-check")
def allocation_check(vehicle_id: int, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    v = _vehicle(db, _org(user), vehicle_id)
    block, warn = _allocation_issues(db, v)
    return {"allowed": not block, "blocked_because": block, "warnings": warn}


@vehicle_router.post("/{vehicle_id}/allocate")
def allocate(vehicle_id: int, body: AllocateIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    """Put the vehicle on a trip. Refused while a mandatory document is expired or missing, or while the vehicle is not free."""
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    block, _warn = _allocation_issues(db, v)
    if block:
        raise HTTPException(409, "This vehicle cannot be allocated: " + "; ".join(block))
    _bump_odometer(db, v, body.odometer, user, "Odometer at allocation")
    _hist(db, v, "driver", v.driver, body.driver or v.driver, user)
    v.driver = body.driver or v.driver
    _set_status(db, v, "ON_TRIP", user, body.reason)
    db.commit()
    return _veh_out(v)


@vehicle_router.post("/{vehicle_id}/release")
def release(vehicle_id: int, body: OdoIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    if v.status != "ON_TRIP":
        raise HTTPException(409, "The vehicle is not on a trip")
    _bump_odometer(db, v, body.odometer, user, "Odometer at trip end")
    _set_status(db, v, "AVAILABLE", user, "Trip ended")
    db.commit()
    return _veh_out(v)


@vehicle_router.post("/{vehicle_id}/odometer")
def record_odometer(vehicle_id: int, body: OdoIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    _bump_odometer(db, v, body.odometer, user, body.reason)
    db.commit()
    return _veh_out(v)


@vehicle_router.post("/{vehicle_id}/status")
def change_status(vehicle_id: int, body: StatusIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    new = body.status.upper()
    if new not in MANUAL_STATUS:
        raise HTTPException(422, "Status can be set to " + ", ".join(sorted(MANUAL_STATUS)) + ". On trip and under maintenance are set by allocation and the workshop")
    if v.status in DISPOSED:
        raise HTTPException(409, "A sold or scrapped vehicle cannot change status")
    if v.status == "UNDER_MAINTENANCE":
        raise HTTPException(409, "The vehicle is in the workshop: close or cancel its work order first")
    if v.status == "ON_TRIP" and new in ("AVAILABLE", "IDLE"):
        raise HTTPException(409, "Use Release to end the trip")
    if new in DISPOSED:
        if not has_permission(user, "fleet.approve"):
            raise HTTPException(403, "Selling or scrapping a vehicle needs approval rights")
        if db.scalar(select(func.count()).select_from(Tyre).where(Tyre.current_vehicle_id == v.id, Tyre.status == "FITTED")):
            raise HTTPException(409, "Remove the fitted tyres before selling or scrapping the vehicle")
    _set_status(db, v, new, user, body.reason)
    record_audit(db, "VEHICLE_STATUS_CHANGED", "FLEET", user, org, "Vehicle", str(v.id), {"status": new, "reason": body.reason})
    db.commit()
    return _veh_out(v)


# ---------------------------------------------------------------- documents
class DocIn(BaseModel):
    doc_type: str = Field(max_length=120)
    number: str | None = Field(default=None, max_length=80)
    issue_date: date | None = None
    expiry_date: date | None = None
    authority: str | None = Field(default=None, max_length=160)
    remarks: str | None = Field(default=None, max_length=300)


@vehicle_router.post("/{vehicle_id}/documents", status_code=201)
def add_document(vehicle_id: int, body: DocIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    """A new document of the same type becomes the current one; the previous stays in the history."""
    org = _org(user)
    v = _vehicle(db, org, vehicle_id, lock=True)
    t = _require_master(db, org, "inv_document_type", body.doc_type, "Document type")
    if (t.data or {}).get("applies_to") == "Container":
        raise HTTPException(422, f"{t.name} is a container document type")
    if (t.data or {}).get("has_expiry_date") == "Yes" and not body.expiry_date:
        raise HTTPException(422, f"{t.name} needs an expiry date")
    if body.issue_date and body.expiry_date and body.expiry_date < body.issue_date:
        raise HTTPException(422, "Expiry date is before the issue date")
    for old in db.scalars(select(VehicleDocument).where(VehicleDocument.vehicle_id == v.id, func.lower(VehicleDocument.doc_type) == t.name.lower(), VehicleDocument.status == "CURRENT")):
        old.status = "SUPERSEDED"
    d = VehicleDocument(organization_id=org, vehicle_id=v.id, doc_type=t.name, number=body.number, issue_date=body.issue_date, expiry_date=body.expiry_date, authority=body.authority,
                        remarks=body.remarks, mandatory=(t.data or {}).get("mandatory") == "Yes", created_by=user.id)
    db.add(d)
    db.flush()
    record_audit(db, "VEHICLE_DOCUMENT_ADDED", "FLEET", user, org, "Vehicle", str(v.id), {"type": t.name, "expiry": str(body.expiry_date)})
    db.commit()
    return _doc_out(d)


@vehicle_router.get("/{vehicle_id}/tyres")
def vehicle_tyres(vehicle_id: int, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    """The wheel layout of this vehicle (positions from its Axle & Wheel Configuration) with the tyre in each position."""
    org = _org(user)
    v = _vehicle(db, org, vehicle_id)
    fitted = {t.current_position.lower(): t for t in db.scalars(select(Tyre).where(Tyre.current_vehicle_id == v.id, Tyre.status == "FITTED")) if t.current_position}
    positions = _positions(db, org, v.axle_config)
    layout = [{"position": p, "tyre": _tyre_out(fitted.pop(p.lower()), v) if p.lower() in fitted else None} for p in positions]
    layout += [{"position": t.current_position, "tyre": _tyre_out(t, v), "not_in_configuration": True} for t in fitted.values()]
    return {"axle_config": v.axle_config, "positions_defined": bool(positions), "layout": layout}


# ---------------------------------------------------------------- work orders
class WoIn(BaseModel):
    vehicle_id: int
    category: str | None = Field(default=None, max_length=120)
    complaint: str | None = Field(default=None, max_length=1000)
    reported_by: str | None = Field(default=None, max_length=120)
    odometer: int | None = Field(default=None, ge=0)
    engine_hours: Money | None = Field(default=None, ge=0)
    workshop: str | None = Field(default=None, max_length=160)
    mechanic: str | None = Field(default=None, max_length=120)


class WoComplete(BaseModel):
    completion_date: date
    inspection_result: str | None = Field(default=None, max_length=1000)
    repair_description: str | None = Field(default=None, max_length=2000)
    parts_note: str | None = Field(default=None, max_length=1000)
    labour_hours: Money | None = Field(default=None, ge=0)
    labour_cost: Money = Field(default=Decimal(0), ge=0)
    material_cost: Money = Field(default=Decimal(0), ge=0)
    external_cost: Money = Field(default=Decimal(0), ge=0)
    other_cost: Money = Field(default=Decimal(0), ge=0)
    odometer: int | None = Field(default=None, ge=0)
    next_service_date: date | None = None
    next_service_odometer: int | None = Field(default=None, ge=0)


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _wo_out(w: VehicleWorkOrder, reg: str | None = None) -> dict:
    return {"id": w.id, "number": w.number, "vehicle_id": w.vehicle_id, "registration_number": reg, "plan_id": w.plan_id, "category": w.category, "complaint": w.complaint, "reported_by": w.reported_by,
            "odometer": w.odometer, "workshop": w.workshop, "mechanic": w.mechanic, "inspection_result": w.inspection_result, "repair_description": w.repair_description, "parts_note": w.parts_note,
            "labour_hours": _m(w.labour_hours), "labour_cost": _m(w.labour_cost), "material_cost": _m(w.material_cost), "external_cost": _m(w.external_cost), "other_cost": _m(w.other_cost), "total_cost": _m(w.total_cost),
            "start_date": w.start_date, "completion_date": w.completion_date, "downtime_days": w.downtime_days, "next_service_date": w.next_service_date, "next_service_odometer": w.next_service_odometer,
            "status": w.status, "status_note": w.status_note, "created_at": w.created_at}


def _regs(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    return {v.id: v.registration_number for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(ids)))} if ids else {}


def _wo(db: Session, org: int, wid: int, lock: bool = False) -> VehicleWorkOrder:
    q = select(VehicleWorkOrder).where(VehicleWorkOrder.id == wid, VehicleWorkOrder.organization_id == org)
    w = db.scalar(q.with_for_update() if lock else q)
    if not w:
        raise HTTPException(404, "Work order not found")
    return w


def _other_open(db: Session, vehicle_id: int, exclude: int) -> int:
    return db.scalar(select(func.count()).select_from(VehicleWorkOrder).where(VehicleWorkOrder.vehicle_id == vehicle_id, VehicleWorkOrder.id != exclude, VehicleWorkOrder.status.in_(("IN_PROGRESS", "COMPLETED")))) or 0


@wo_router.get("")
def list_work_orders(q: str | None = None, status: str | None = Query(default=None, pattern="^(OPEN|IN_PROGRESS|COMPLETED|CLOSED|CANCELLED)$"), vehicle_id: int | None = None,
                     limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    base = select(VehicleWorkOrder).where(VehicleWorkOrder.organization_id == org, *([VehicleWorkOrder.status == status] if status else []), *([VehicleWorkOrder.vehicle_id == vehicle_id] if vehicle_id else []))
    if q:
        base = base.join(Vehicle, Vehicle.id == VehicleWorkOrder.vehicle_id).where(or_(Vehicle.registration_number.ilike(f"%{_reg(q) or q}%"), VehicleWorkOrder.number.ilike(f"%{q}%")))
    total, rows = _page(base, db, limit, offset, VehicleWorkOrder.id.desc())
    regs = _regs(db, [r.vehicle_id for r in rows])
    return {"items": [_wo_out(r, regs.get(r.vehicle_id)) for r in rows], "total": total}


def _new_wo(db: Session, user: User, org: int, v: Vehicle, body: dict, plan_id: int | None = None) -> VehicleWorkOrder:
    w = VehicleWorkOrder(organization_id=org, vehicle_id=v.id, plan_id=plan_id, created_by=user.id, **body)
    w.number = next_number(db, org, "vehicle_work_order", "VWO", _today(), user=user)
    db.add(w)
    db.flush()
    record_audit(db, "VEHICLE_WORK_ORDER_CREATED", "FLEET", user, org, "VehicleWorkOrder", str(w.id), {"number": w.number, "vehicle": v.registration_number})
    return w


@wo_router.post("", status_code=201)
def create_work_order(body: WoIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, body.vehicle_id)
    if v.status in DISPOSED:
        raise HTTPException(409, "A sold or scrapped vehicle cannot be sent to the workshop")
    if not (body.complaint or body.category):
        raise HTTPException(422, "Describe the complaint or choose a maintenance category")
    if body.category:
        _require_master(db, org, "inv_maintenance_category", body.category, "Maintenance category")
    w = _new_wo(db, user, org, v, body.model_dump(exclude={"vehicle_id"}))
    db.commit()
    return _wo_out(w, v.registration_number)


@wo_router.get("/{wo_id}")
def get_work_order(wo_id: int, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    w = _wo(db, _org(user), wo_id)
    return _wo_out(w, _regs(db, [w.vehicle_id]).get(w.vehicle_id))


@wo_router.post("/{wo_id}/start")
def start_work_order(wo_id: int, start_date: date | None = None, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    w = _wo(db, org, wo_id, lock=True)
    if w.status != "OPEN":
        raise HTTPException(409, f"A work order that is {w.status.lower().replace('_', ' ')} cannot be started")
    v = _vehicle(db, org, w.vehicle_id, lock=True)
    if v.status == "ON_TRIP":
        raise HTTPException(409, f"Vehicle {v.registration_number} is on a trip: end the trip first")
    if v.status in DISPOSED:
        raise HTTPException(409, "A sold or scrapped vehicle cannot be repaired")
    _bump_odometer(db, v, w.odometer, user, f"Odometer at work order {w.number}")
    w.status, w.start_date = "IN_PROGRESS", start_date or _today()
    _set_status(db, v, "UNDER_MAINTENANCE", user, f"Work order {w.number} started")
    db.commit()
    return _wo_out(w, v.registration_number)


@wo_router.post("/{wo_id}/complete")
def complete_work_order(wo_id: int, body: WoComplete, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    w = _wo(db, org, wo_id, lock=True)
    if w.status != "IN_PROGRESS":
        raise HTTPException(409, "Only a work order in progress can be completed (a completed one is never completed twice, so costs are not counted twice)")
    if body.completion_date < (w.start_date or body.completion_date):
        raise HTTPException(422, "Completion date is before the start date")
    v = _vehicle(db, org, w.vehicle_id, lock=True)
    _bump_odometer(db, v, body.odometer, user, f"Odometer at completion of {w.number}")
    for k in ("inspection_result", "repair_description", "parts_note", "labour_hours", "labour_cost", "material_cost", "external_cost", "other_cost", "next_service_date", "next_service_odometer"):
        setattr(w, k, getattr(body, k))
    w.odometer = body.odometer or w.odometer
    w.total_cost = body.labour_cost + body.material_cost + body.external_cost + body.other_cost
    w.status, w.completion_date = "COMPLETED", body.completion_date
    w.downtime_days = (body.completion_date - (w.start_date or body.completion_date)).days + 1
    db.commit()
    return _wo_out(w, v.registration_number)


@wo_router.post("/{wo_id}/close")
def close_work_order(wo_id: int, user: User = Depends(permission_required("fleet.approve")), db: Session = Depends(get_db)):
    """Approval of the finished job: the vehicle comes back into service and the maintenance plan it served is reset."""
    org = _org(user)
    w = _wo(db, org, wo_id, lock=True)
    if w.status != "COMPLETED":
        raise HTTPException(409, "Only a completed work order can be closed")
    if _sod_blocked(db, user, org, w.created_by):
        raise HTTPException(403, "You raised this work order; another approver must close it")
    v = _vehicle(db, org, w.vehicle_id, lock=True)
    w.status, w.approved_by = "CLOSED", user.id
    if not _other_open(db, v.id, w.id) and v.status == "UNDER_MAINTENANCE":
        _set_status(db, v, "AVAILABLE", user, f"Work order {w.number} closed")
    if w.plan_id:
        p = db.get(VehiclePlan, w.plan_id)
        if p:
            p.last_done_odometer, p.last_done_date = (w.odometer if w.odometer is not None else v.odometer), w.completion_date
    record_audit(db, "VEHICLE_WORK_ORDER_CLOSED", "FLEET", user, org, "VehicleWorkOrder", str(w.id), {"number": w.number, "cost": str(w.total_cost)})
    db.commit()
    return _wo_out(w, v.registration_number)


@wo_router.post("/{wo_id}/cancel")
def cancel_work_order(wo_id: int, body: Reason, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    w = _wo(db, org, wo_id, lock=True)
    if w.status not in ("OPEN", "IN_PROGRESS"):
        raise HTTPException(409, "Only an open or in-progress work order can be cancelled")
    if w.status == "IN_PROGRESS" and not has_permission(user, "fleet.approve"):
        raise HTTPException(403, "Only an approver can cancel a job that has started")
    v = _vehicle(db, org, w.vehicle_id, lock=True)
    was = w.status
    w.status, w.status_note = "CANCELLED", body.reason
    if was == "IN_PROGRESS" and not _other_open(db, v.id, w.id) and v.status == "UNDER_MAINTENANCE":
        _set_status(db, v, "AVAILABLE", user, f"Work order {w.number} cancelled")
    db.commit()
    return _wo_out(w, v.registration_number)


# ---------------------------------------------------------------- preventive maintenance plans
class PlanIn(BaseModel):
    vehicle_id: int
    name: str = Field(min_length=2, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    interval_km: int | None = Field(default=None, ge=1)
    interval_days: int | None = Field(default=None, ge=1)
    last_done_odometer: int | None = Field(default=None, ge=0)
    last_done_date: date | None = None


def _plan_out(p: VehiclePlan, v: Vehicle) -> dict:
    return {"id": p.id, "vehicle_id": p.vehicle_id, "registration_number": v.registration_number, "name": p.name, "category": p.category, "interval_km": p.interval_km, "interval_days": p.interval_days,
            "last_done_odometer": p.last_done_odometer, "last_done_date": p.last_done_date, "current_odometer": v.odometer, "is_active": p.is_active, **_plan_due(p, v)}


@plan_router.get("")
def list_plans(due: bool = False, vehicle_id: int | None = None, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    q = select(VehiclePlan, Vehicle).join(Vehicle, Vehicle.id == VehiclePlan.vehicle_id).where(VehiclePlan.organization_id == org, VehiclePlan.is_active.is_(True), Vehicle.is_active.is_(True), Vehicle.status.notin_(list(DISPOSED)))
    if vehicle_id:
        q = q.where(VehiclePlan.vehicle_id == vehicle_id)
    rows = [_plan_out(p, v) for p, v in db.execute(q.order_by(Vehicle.registration_number, VehiclePlan.id))]
    if due:
        rows = [r for r in rows if r["overdue"] or r["due_soon"]]
    return {"items": rows, "total": len(rows)}


@plan_router.post("", status_code=201)
def create_plan(body: PlanIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    v = _vehicle(db, org, body.vehicle_id)
    if not (body.interval_km or body.interval_days):
        raise HTTPException(422, "Set an interval in kilometres, in days, or both")
    if body.category:
        _require_master(db, org, "inv_maintenance_category", body.category, "Maintenance category")
    p = VehiclePlan(organization_id=org, created_by=user.id, **{**body.model_dump(), "last_done_odometer": body.last_done_odometer if body.last_done_odometer is not None else v.odometer,
                                                                "last_done_date": body.last_done_date or _today()})
    db.add(p)
    db.commit()
    return _plan_out(p, v)


def _generate(db: Session, user: User, org: int, p: VehiclePlan, v: Vehicle) -> VehicleWorkOrder | None:
    """One work order per due interval: nothing is created while an earlier one for the same plan is still open."""
    if db.scalar(select(VehicleWorkOrder.id).where(VehicleWorkOrder.plan_id == p.id, VehicleWorkOrder.status.in_(OPEN_WO))):
        return None
    return _new_wo(db, user, org, v, {"category": p.category, "complaint": f"Preventive maintenance: {p.name}", "odometer": v.odometer, "reported_by": "Maintenance plan"}, plan_id=p.id)


@plan_router.post("/{plan_id}/generate", status_code=201)
def generate_for_plan(plan_id: int, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    p = db.scalar(select(VehiclePlan).where(VehiclePlan.id == plan_id, VehiclePlan.organization_id == org).with_for_update())
    if not p:
        raise HTTPException(404, "Plan not found")
    v = _vehicle(db, org, p.vehicle_id)
    due = _plan_due(p, v)
    if not (due["overdue"] or due["due_soon"]):
        raise HTTPException(409, "This service is not due yet")
    w = _generate(db, user, org, p, v)
    if not w:
        raise HTTPException(409, "A work order for this service is already open")
    db.commit()
    return _wo_out(w, v.registration_number)


@plan_router.post("/generate-due")
def generate_all_due(user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    """Create the work orders for every service that is due or about to be, skipping the ones that already have an open order."""
    org = _org(user)
    made, skipped = [], 0
    for p, v in db.execute(select(VehiclePlan, Vehicle).join(Vehicle, Vehicle.id == VehiclePlan.vehicle_id).where(VehiclePlan.organization_id == org, VehiclePlan.is_active.is_(True), Vehicle.is_active.is_(True), Vehicle.status.notin_(list(DISPOSED)))):
        d = _plan_due(p, v)
        if d["overdue"] or d["due_soon"]:
            w = _generate(db, user, org, p, v)
            if w:
                made.append({"number": w.number, "registration_number": v.registration_number, "plan": p.name})
            else:
                skipped += 1
    db.commit()
    return {"created": made, "skipped_already_open": skipped}


# ---------------------------------------------------------------- tyres
class TyreIn(BaseModel):
    serial_number: str = Field(min_length=2, max_length=60)
    brand: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)
    condition_type: str = Field(default="NEW", pattern="^(NEW|RETREAD)$")
    purchase_date: date | None = None
    purchase_cost: Money | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=160)
    warranty_until: date | None = None
    tread_depth_mm: Money | None = Field(default=None, ge=0, le=40)
    warehouse: str | None = Field(default=None, max_length=120)
    remarks: str | None = Field(default=None, max_length=500)


class FitIn(BaseModel):
    vehicle_id: int
    position: str = Field(min_length=1, max_length=60)
    odometer: int = Field(ge=0)
    tread_depth_mm: Money | None = Field(default=None, ge=0, le=40)


class RemoveIn(BaseModel):
    odometer: int = Field(ge=0)
    to_status: str = Field(pattern="^(IN_STOCK|UNDER_REPAIR|RETREADING|SCRAPPED)$")
    reason: str = Field(min_length=3, max_length=300)
    tread_depth_mm: Money | None = Field(default=None, ge=0, le=40)
    cost: Money | None = Field(default=None, ge=0)


class RotateIn(BaseModel):
    position: str = Field(min_length=1, max_length=60)
    odometer: int = Field(ge=0)


class ReceiveIn(BaseModel):
    tread_depth_mm: Money | None = Field(default=None, ge=0, le=40)
    cost: Money | None = Field(default=None, ge=0)
    warehouse: str | None = Field(default=None, max_length=120)


class ScrapIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _tyre_out(t: Tyre, v: Vehicle | None = None) -> dict:
    running = (v.odometer - t.fitted_odometer) if (t.status == "FITTED" and v is not None and t.fitted_odometer is not None) else 0
    km = t.total_km + max(running, 0)
    cost = (t.purchase_cost or Decimal(0)) + (t.extra_cost or Decimal(0))
    return {"id": t.id, "serial_number": t.serial_number, "brand": t.brand, "category": t.category, "model": t.model, "size": t.size, "condition_type": t.condition_type, "purchase_date": t.purchase_date,
            "purchase_cost": _m(t.purchase_cost), "supplier": t.supplier, "warranty_until": t.warranty_until, "tread_depth_mm": _m(t.tread_depth_mm), "status": t.status, "warehouse": t.warehouse,
            "current_vehicle_id": t.current_vehicle_id, "registration_number": v.registration_number if v else None, "current_position": t.current_position, "fitted_odometer": t.fitted_odometer,
            "retread_count": t.retread_count, "total_km": km, "total_cost": str(cost), "cost_per_km": str((cost / km).quantize(Decimal("0.0001"))) if km > 0 and cost > 0 else None,
            "scrap_reason": t.scrap_reason, "remarks": t.remarks}


def _tyre(db: Session, org: int, tid: int, lock: bool = False) -> Tyre:
    q = select(Tyre).where(Tyre.id == tid, Tyre.organization_id == org)
    t = db.scalar(q.with_for_update() if lock else q)
    if not t:
        raise HTTPException(404, "Tyre not found")
    return t


def _tevent(db: Session, t: Tyre, event: str, user: User, **kw) -> None:
    db.add(TyreEvent(organization_id=t.organization_id, tyre_id=t.id, event=event, user_id=user.id, **kw))


@tyre_router.get("")
def list_tyres(q: str | None = None, status: str | None = Query(default=None, pattern="^(IN_STOCK|FITTED|UNDER_REPAIR|RETREADING|SCRAPPED)$"), brand: str | None = None, vehicle_id: int | None = None,
               limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [Tyre.organization_id == org]
    for col, val in ((Tyre.status, status), (Tyre.brand, brand), (Tyre.current_vehicle_id, vehicle_id)):
        if val:
            w.append(col == val)
    if q:
        w.append(or_(Tyre.serial_number.ilike(f"%{q.strip()}%"), Tyre.model.ilike(f"%{q.strip()}%")))
    total, rows = _page(select(Tyre).where(*w), db, limit, offset, Tyre.serial_number)
    vs = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_({t.current_vehicle_id for t in rows if t.current_vehicle_id})))} if rows else {}
    return {"items": [_tyre_out(t, vs.get(t.current_vehicle_id)) for t in rows], "total": total}


@tyre_router.post("", status_code=201)
def create_tyre(body: TyreIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    serial = body.serial_number.strip().upper()
    if db.scalar(select(Tyre.id).where(Tyre.organization_id == org, Tyre.serial_number == serial)):
        raise HTTPException(409, f"Tyre {serial} is already registered")
    _require_master(db, org, "inv_tyre_brand", body.brand, "Tyre brand")
    _require_master(db, org, "inv_tyre_category", body.category, "Tyre category")
    _require_master(db, org, "inv_warehouse", body.warehouse, "Warehouse")
    t = Tyre(organization_id=org, created_by=user.id, **{**body.model_dump(), "serial_number": serial})
    db.add(t)
    db.flush()
    _tevent(db, t, "RECEIVED", user, tread_depth_mm=t.tread_depth_mm, cost=t.purchase_cost, reason="Registered in stock")
    db.commit()
    return _tyre_out(t)


@tyre_router.get("/{tyre_id}")
def get_tyre(tyre_id: int, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    t = _tyre(db, org, tyre_id)
    v = db.get(Vehicle, t.current_vehicle_id) if t.current_vehicle_id else None
    ev = db.scalars(select(TyreEvent).where(TyreEvent.tyre_id == t.id).order_by(TyreEvent.id.desc())).all()
    regs = _regs(db, [e.vehicle_id for e in ev])
    return {**_tyre_out(t, v), "history": [{"event": e.event, "registration_number": regs.get(e.vehicle_id), "position": e.position, "from_position": e.from_position, "odometer": e.odometer,
                                            "tread_depth_mm": _m(e.tread_depth_mm), "cost": _m(e.cost), "reason": e.reason, "at": e.created_at} for e in ev]}


@tyre_router.post("/{tyre_id}/fit")
def fit_tyre(tyre_id: int, body: FitIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    t = _tyre(db, org, tyre_id, lock=True)
    if t.status != "IN_STOCK":
        raise HTTPException(409, f"Tyre {t.serial_number} is {t.status.replace('_', ' ').lower()}" + (f" on a vehicle at {t.current_position}" if t.status == "FITTED" else "") + " and cannot be fitted")
    v = _vehicle(db, org, body.vehicle_id, lock=True)
    if v.status in DISPOSED:
        raise HTTPException(409, "Tyres cannot be fitted to a sold or scrapped vehicle")
    positions = _positions(db, org, v.axle_config)
    if not positions:
        raise HTTPException(409, f"Vehicle {v.registration_number} has no wheel positions: choose an Axle & Wheel Configuration with Position Labels for it")
    pos = next((p for p in positions if p.lower() == body.position.strip().lower()), None)
    if not pos:
        raise HTTPException(422, f"Position {body.position} is not in the vehicle's configuration ({', '.join(positions)})")
    if db.scalar(select(Tyre.id).where(Tyre.current_vehicle_id == v.id, Tyre.status == "FITTED", func.lower(Tyre.current_position) == pos.lower())):
        raise HTTPException(409, f"Position {pos} on {v.registration_number} already has a tyre fitted")
    _bump_odometer(db, v, body.odometer, user, f"Tyre {t.serial_number} fitted at {pos}")
    t.status, t.current_vehicle_id, t.current_position, t.fitted_odometer, t.warehouse = "FITTED", v.id, pos, body.odometer, None
    if body.tread_depth_mm is not None:
        t.tread_depth_mm = body.tread_depth_mm
    _tevent(db, t, "FITTED", user, vehicle_id=v.id, position=pos, odometer=body.odometer, tread_depth_mm=t.tread_depth_mm)
    db.commit()
    return _tyre_out(t, v)


@tyre_router.post("/{tyre_id}/remove")
def remove_tyre(tyre_id: int, body: RemoveIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    t = _tyre(db, org, tyre_id, lock=True)
    if t.status != "FITTED":
        raise HTTPException(409, "Only a fitted tyre can be removed")
    v = _vehicle(db, org, t.current_vehicle_id, lock=True)
    if body.odometer < (t.fitted_odometer or 0):
        raise HTTPException(409, f"Odometer {body.odometer} is lower than where the tyre was fitted ({t.fitted_odometer})")
    _bump_odometer(db, v, body.odometer, user, f"Tyre {t.serial_number} removed from {t.current_position}")
    t.total_km += body.odometer - (t.fitted_odometer or body.odometer)
    pos = t.current_position
    t.status, t.current_vehicle_id, t.current_position, t.fitted_odometer = body.to_status, None, None, None
    if body.tread_depth_mm is not None:
        t.tread_depth_mm = body.tread_depth_mm
    if body.cost:
        t.extra_cost = (t.extra_cost or Decimal(0)) + body.cost
    if body.to_status == "SCRAPPED":
        t.scrap_reason = body.reason
    _tevent(db, t, "SCRAPPED" if body.to_status == "SCRAPPED" else "REMOVED", user, vehicle_id=v.id, position=pos, odometer=body.odometer, tread_depth_mm=t.tread_depth_mm, cost=body.cost, reason=f"{body.reason} -> {body.to_status}")
    db.commit()
    return _tyre_out(t)


@tyre_router.post("/{tyre_id}/rotate")
def rotate_tyre(tyre_id: int, body: RotateIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    org = _org(user)
    t = _tyre(db, org, tyre_id, lock=True)
    if t.status != "FITTED":
        raise HTTPException(409, "Only a fitted tyre can be rotated")
    v = _vehicle(db, org, t.current_vehicle_id, lock=True)
    positions = _positions(db, org, v.axle_config)
    pos = next((p for p in positions if p.lower() == body.position.strip().lower()), None)
    if not pos:
        raise HTTPException(422, f"Position {body.position} is not in the vehicle's configuration")
    if pos.lower() == (t.current_position or "").lower():
        raise HTTPException(409, "The tyre is already in that position")
    if db.scalar(select(Tyre.id).where(Tyre.current_vehicle_id == v.id, Tyre.status == "FITTED", func.lower(Tyre.current_position) == pos.lower())):
        raise HTTPException(409, f"Position {pos} already has a tyre: remove it or rotate it first")
    _bump_odometer(db, v, body.odometer, user, f"Tyre {t.serial_number} rotated")
    old = t.current_position
    t.total_km += max(body.odometer - (t.fitted_odometer or body.odometer), 0)
    t.current_position, t.fitted_odometer = pos, body.odometer
    _tevent(db, t, "ROTATED", user, vehicle_id=v.id, position=pos, from_position=old, odometer=body.odometer)
    db.commit()
    return _tyre_out(t, v)


@tyre_router.post("/{tyre_id}/receive")
def receive_back(tyre_id: int, body: ReceiveIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    """A tyre comes back from repair or retreading and goes into stock."""
    org = _org(user)
    t = _tyre(db, org, tyre_id, lock=True)
    if t.status not in ("UNDER_REPAIR", "RETREADING"):
        raise HTTPException(409, "Only a tyre that is under repair or at the retreader can be received back")
    _require_master(db, org, "inv_warehouse", body.warehouse, "Warehouse")
    if t.status == "RETREADING":
        t.retread_count += 1
        t.condition_type = "RETREAD"
    t.status, t.warehouse = "IN_STOCK", body.warehouse
    if body.tread_depth_mm is not None:
        t.tread_depth_mm = body.tread_depth_mm
    if body.cost:
        t.extra_cost = (t.extra_cost or Decimal(0)) + body.cost
    _tevent(db, t, "RETREAD_BACK", user, tread_depth_mm=t.tread_depth_mm, cost=body.cost, reason="Received back into stock")
    db.commit()
    return _tyre_out(t)


@tyre_router.post("/{tyre_id}/scrap")
def scrap_tyre(tyre_id: int, body: ScrapIn, user: User = Depends(permission_required("fleet.approve")), db: Session = Depends(get_db)):
    org = _org(user)
    t = _tyre(db, org, tyre_id, lock=True)
    if t.status == "FITTED":
        raise HTTPException(409, "Remove the tyre from the vehicle first")
    if t.status == "SCRAPPED":
        raise HTTPException(409, "Already scrapped")
    t.status, t.scrap_reason, t.warehouse = "SCRAPPED", body.reason, None
    _tevent(db, t, "SCRAPPED", user, reason=body.reason)
    db.commit()
    return _tyre_out(t)


# ---------------------------------------------------------------- fuel
class FuelIn(BaseModel):
    vehicle_id: int
    kind: str = Field(default="FUEL", pattern="^(FUEL|LUBRICANT)$")
    txn_date: date
    odometer: int | None = Field(default=None, ge=0)
    litres: Money = Field(gt=0)
    amount: Money | None = Field(default=None, ge=0)
    fuel_station: str | None = Field(default=None, max_length=120)
    fuel_card: str | None = Field(default=None, max_length=60)
    driver: str | None = Field(default=None, max_length=120)
    full_tank: bool = True


def _fuel_out(f: FuelTransaction, reg: str | None = None) -> dict:
    return {"id": f.id, "vehicle_id": f.vehicle_id, "registration_number": reg, "kind": f.kind, "txn_date": f.txn_date, "odometer": f.odometer, "litres": _m(f.litres), "amount": _m(f.amount),
            "fuel_station": f.fuel_station, "fuel_card": f.fuel_card, "driver": f.driver, "full_tank": f.full_tank, "km_per_litre": _m(f.km_per_litre), "abnormal": f.abnormal,
            "cost_per_litre": str((f.amount / f.litres).quantize(Decimal("0.01"))) if f.amount and f.litres else None}


@fuel_router.post("", status_code=201)
def add_fuel(body: FuelIn, user: User = Depends(permission_required("fleet.create")), db: Session = Depends(get_db)):
    """Fuel is measured from one full tank to the next: kilometres since the last fill divided by the litres put in now. A reading lower than the last one is refused."""
    org = _org(user)
    v = _vehicle(db, org, body.vehicle_id, lock=True)
    if v.status in DISPOSED:
        raise HTTPException(409, "A sold or scrapped vehicle cannot take fuel")
    f = FuelTransaction(organization_id=org, created_by=user.id, **body.model_dump())
    if body.kind == "FUEL":
        if body.odometer is None:
            raise HTTPException(422, "Enter the odometer reading for a fuel fill")
        if body.odometer < v.odometer:
            raise HTTPException(409, f"Odometer reading {body.odometer} is lower than the vehicle's recorded {v.odometer}")
        prev = db.scalar(select(FuelTransaction).where(FuelTransaction.vehicle_id == v.id, FuelTransaction.kind == "FUEL", FuelTransaction.odometer.is_not(None), FuelTransaction.full_tank.is_(True)).order_by(FuelTransaction.odometer.desc()).limit(1))
        if prev and body.full_tank and body.odometer > prev.odometer:
            f.km_per_litre = ((Decimal(body.odometer - prev.odometer)) / body.litres).quantize(Decimal("0.01"))
            history = [x for x in db.scalars(select(FuelTransaction.km_per_litre).where(FuelTransaction.vehicle_id == v.id, FuelTransaction.km_per_litre.is_not(None)).order_by(FuelTransaction.id.desc()).limit(5))]
            if len(history) >= 3 and f.km_per_litre < (sum(history) / len(history)) * Decimal("0.7"):
                f.abnormal = True
        _bump_odometer(db, v, body.odometer, user, "Odometer at fuel fill")
    db.add(f)
    db.flush()
    db.commit()
    return _fuel_out(f, v.registration_number)


@fuel_router.get("/summary")
def fuel_summary(date_from: date | None = None, date_to: date | None = None, user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    """Per vehicle: litres, cost, kilometres between first and last full-tank reading, average km per litre and cost per km, and how many abnormal fills."""
    org = _org(user)
    w = [FuelTransaction.organization_id == org, FuelTransaction.kind == "FUEL"]
    if date_from:
        w.append(FuelTransaction.txn_date >= date_from)
    if date_to:
        w.append(FuelTransaction.txn_date <= date_to)
    rows = db.scalars(select(FuelTransaction).where(*w).order_by(FuelTransaction.vehicle_id, FuelTransaction.odometer)).all()
    regs = _regs(db, [r.vehicle_id for r in rows])
    out: dict[int, dict] = {}
    for r in rows:
        o = out.setdefault(r.vehicle_id, {"vehicle_id": r.vehicle_id, "registration_number": regs.get(r.vehicle_id), "litres": Decimal(0), "amount": Decimal(0), "fills": 0, "abnormal": 0, "odos": [], "kpl": []})
        o["litres"] += r.litres
        o["amount"] += r.amount or 0
        o["fills"] += 1
        o["abnormal"] += 1 if r.abnormal else 0
        if r.odometer is not None:
            o["odos"].append(r.odometer)
        if r.km_per_litre is not None:
            o["kpl"].append(r.km_per_litre)
    items = []
    for o in out.values():
        km = (max(o["odos"]) - min(o["odos"])) if len(o["odos"]) >= 2 else 0
        items.append({"vehicle_id": o["vehicle_id"], "registration_number": o["registration_number"], "fills": o["fills"], "litres": str(o["litres"]), "amount": str(o["amount"]), "km": km,
                      "average_km_per_litre": str((sum(o["kpl"]) / len(o["kpl"])).quantize(Decimal("0.01"))) if o["kpl"] else None,
                      "cost_per_km": str((o["amount"] / km).quantize(Decimal("0.01"))) if km and o["amount"] else None, "abnormal_fills": o["abnormal"]})
    return {"items": sorted(items, key=lambda x: x["registration_number"] or ""), "total_litres": str(sum((o["litres"] for o in out.values()), Decimal(0))), "total_amount": str(sum((o["amount"] for o in out.values()), Decimal(0)))}


@fuel_router.get("")
def list_fuel(vehicle_id: int | None = None, abnormal: bool | None = None, kind: str | None = Query(default=None, pattern="^(FUEL|LUBRICANT)$"), date_from: date | None = None, date_to: date | None = None,
              limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    w = [FuelTransaction.organization_id == org]
    if vehicle_id:
        w.append(FuelTransaction.vehicle_id == vehicle_id)
    if abnormal is not None:
        w.append(FuelTransaction.abnormal.is_(abnormal))
    if kind:
        w.append(FuelTransaction.kind == kind)
    if date_from:
        w.append(FuelTransaction.txn_date >= date_from)
    if date_to:
        w.append(FuelTransaction.txn_date <= date_to)
    total, rows = _page(select(FuelTransaction).where(*w), db, limit, offset, FuelTransaction.id.desc())
    regs = _regs(db, [r.vehicle_id for r in rows])
    return {"items": [_fuel_out(r, regs.get(r.vehicle_id)) for r in rows], "total": total}


# ---------------------------------------------------------------- alerts
@alert_router.get("")
def fleet_alerts(days: int = Query(default=60, ge=1, le=730), min_tread_mm: Decimal = Query(default=Decimal("3.0"), ge=0), user: User = Depends(permission_required("fleet.read")), db: Session = Depends(get_db)):
    org = _org(user)
    today = _today()
    docs = [{"vehicle_id": v.id, "registration_number": v.registration_number, "doc_type": d.doc_type, "expiry_date": d.expiry_date, "days_left": (d.expiry_date - today).days, "expired": d.expiry_date < today, "mandatory": d.mandatory}
            for d, v in db.execute(select(VehicleDocument, Vehicle).join(Vehicle, Vehicle.id == VehicleDocument.vehicle_id).where(VehicleDocument.organization_id == org, VehicleDocument.status == "CURRENT",
                                                                                                                                  VehicleDocument.expiry_date.is_not(None), VehicleDocument.expiry_date <= today + timedelta(days=days),
                                                                                                                                  Vehicle.is_active.is_(True), Vehicle.status.notin_(list(DISPOSED))).order_by(VehicleDocument.expiry_date).limit(500))]
    plans = [r for r in (_plan_out(p, v) for p, v in db.execute(select(VehiclePlan, Vehicle).join(Vehicle, Vehicle.id == VehiclePlan.vehicle_id).where(VehiclePlan.organization_id == org, VehiclePlan.is_active.is_(True),
                                                                                                                                              Vehicle.is_active.is_(True), Vehicle.status.notin_(list(DISPOSED))))) if r["overdue"] or r["due_soon"]]
    tyres = [{"serial_number": t.serial_number, "registration_number": v.registration_number, "position": t.current_position, "tread_depth_mm": _m(t.tread_depth_mm)}
             for t, v in db.execute(select(Tyre, Vehicle).join(Vehicle, Vehicle.id == Tyre.current_vehicle_id).where(Tyre.organization_id == org, Tyre.status == "FITTED", Tyre.tread_depth_mm.is_not(None), Tyre.tread_depth_mm <= min_tread_mm))]
    cnt = lambda model, *w: db.scalar(select(func.count()).select_from(model).where(model.organization_id == org, *w)) or 0
    pending = {"work_orders_open": cnt(VehicleWorkOrder, VehicleWorkOrder.status == "OPEN"), "work_orders_in_progress": cnt(VehicleWorkOrder, VehicleWorkOrder.status == "IN_PROGRESS"),
               "work_orders_to_close": cnt(VehicleWorkOrder, VehicleWorkOrder.status == "COMPLETED"), "breakdown_or_accident": cnt(Vehicle, Vehicle.status.in_(("BREAKDOWN", "ACCIDENT"))),
               "abnormal_fuel_30_days": cnt(FuelTransaction, FuelTransaction.abnormal.is_(True), FuelTransaction.txn_date >= today - timedelta(days=30))}
    return {"days": days, "documents": docs, "plans": plans, "tyres_low_tread": tyres, "pending": pending, "total": len(docs) + len(plans) + len(tyres) + sum(pending.values())}
