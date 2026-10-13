import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import has_permission, permission_required
from .auth_api import record_audit
from .billing import next_number
from .db import get_db
from .models import Booking, Customer, Shipment, User
from .schemas_auth import _clean_party_data

router = APIRouter(prefix="/api/bookings", tags=["bookings"])
MODES = "^(Sea|Air|Road|Rail|Multimodal|Courier)$"
DIRECTIONS = "^(Export|Import|Domestic|Cross-Trade)$"
STATUSES = "^(DRAFT|CONFIRMED|CANCELLED|SPACE_RELEASED)$"


class BookingIn(BaseModel):
    booking_date: date | None = None
    mode: str = Field(default="Sea", pattern=MODES)
    direction: str = Field(default="Export", pattern=DIRECTIONS)
    shipment_type: str | None = Field(default=None, max_length=40)
    booking_type: str | None = Field(default=None, pattern="^(Direct|House)$")
    hbl_type: str | None = Field(default=None, max_length=60)
    scac: str | None = Field(default=None, max_length=20)
    customer_id: int | None = None
    party_name: str = Field(default="", max_length=200)
    carrier: str | None = Field(default=None, max_length=160)
    carrier_booking_no: str | None = Field(default=None, max_length=80)
    vessel: str | None = Field(default=None, max_length=120)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    etd: date | None = None
    eta: date | None = None
    si_cutoff: date | None = None
    vgm_cutoff: date | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class BookingPatch(BaseModel):
    booking_date: date | None = None
    mode: str | None = Field(default=None, pattern=MODES)
    direction: str | None = Field(default=None, pattern=DIRECTIONS)
    shipment_type: str | None = Field(default=None, max_length=40)
    booking_type: str | None = Field(default=None, pattern="^(Direct|House)$")
    hbl_type: str | None = Field(default=None, max_length=60)
    scac: str | None = Field(default=None, max_length=20)
    customer_id: int | None = None
    party_name: str | None = Field(default=None, max_length=200)
    carrier: str | None = Field(default=None, max_length=160)
    carrier_booking_no: str | None = Field(default=None, max_length=80)
    vessel: str | None = Field(default=None, max_length=120)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    etd: date | None = None
    eta: date | None = None
    si_cutoff: date | None = None
    vgm_cutoff: date | None = None
    data: dict[str, Any] | None = None


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bookings are tenant-scoped; Super Admin does not manage them")
    return user.organization_id


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return _clean_party_data(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _out(b: Booking) -> dict:
    return {"id": b.id, "number": b.number, "status": b.status, "booking_date": b.booking_date, "mode": b.mode, "direction": b.direction, "shipment_type": b.shipment_type, "booking_type": b.booking_type, "hbl_type": b.hbl_type, "scac": b.scac,
            "customer_id": b.customer_id, "party_name": b.party_name, "carrier": b.carrier, "carrier_booking_no": b.carrier_booking_no, "vessel": b.vessel, "pol": b.pol, "pod": b.pod, "etd": b.etd, "eta": b.eta,
            "si_cutoff": b.si_cutoff, "vgm_cutoff": b.vgm_cutoff, "job_number": b.job_number, "data": b.data or {}, "confirmed_at": b.confirmed_at, "closed_at": b.closed_at, "created_at": b.created_at}


def _get(db: Session, org_id: int, booking_id: int, lock: bool = False) -> Booking:
    query = select(Booking).where(Booking.id == booking_id, Booking.organization_id == org_id)
    if lock:
        query = query.with_for_update()
    b = db.scalar(query)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    return b


def _check_customer(db: Session, org_id: int, customer_id: int | None) -> None:
    if customer_id and not db.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.organization_id == org_id)):
        raise HTTPException(status_code=404, detail="Customer not found")


def _check_dates(b: Booking) -> None:
    if b.etd and b.eta and b.eta < b.etd:
        raise HTTPException(status_code=422, detail="ETA cannot be earlier than ETD")


@router.get("")
def list_bookings(status_filter: str | None = Query(default=None, alias="status", pattern=STATUSES), q: str | None = Query(default=None, max_length=100), limit: int = Query(default=300, ge=1, le=1000),
                  user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    query = select(Booking).where(Booking.organization_id == org_id)
    if status_filter:
        query = query.where(Booking.status == status_filter)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Booking.number.ilike(like), Booking.party_name.ilike(like), Booking.carrier.ilike(like), Booking.job_number.ilike(like), Booking.carrier_booking_no.ilike(like)))
    return [_out(b) for b in db.scalars(query.order_by(Booking.booking_date.desc(), Booking.id.desc()).limit(limit))]


@router.get("/summary")
def summary(user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    today = date.today()
    soon = today + timedelta(days=2)
    week = today - timedelta(days=7)
    rows = list(db.scalars(select(Booking).where(Booking.organization_id == org_id)))
    confirmed = [b for b in rows if b.status == "CONFIRMED"]
    return {"confirmed_this_week": sum(1 for b in confirmed if b.confirmed_at and b.confirmed_at.date() >= week),
            "si_cutoff_48h": sum(1 for b in confirmed if b.si_cutoff and today <= b.si_cutoff <= soon),
            "vgm_pending": sum(1 for b in confirmed if b.vgm_cutoff and b.vgm_cutoff >= today),
            "space_released": sum(1 for b in rows if b.status == "SPACE_RELEASED")}


@router.post("", status_code=201)
def create_booking(payload: BookingIn, user: User = Depends(permission_required("booking.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    _check_customer(db, org_id, payload.customer_id)
    values = payload.model_dump(exclude={"data", "booking_date"})
    b = Booking(organization_id=org_id, status="DRAFT", booking_date=payload.booking_date or date.today(), data=_clean(payload.data), created_by=user.id, **values)
    _check_dates(b)
    db.add(b)
    db.flush()
    record_audit(db, "BOOKING_CREATED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"party": b.party_name})
    db.commit()
    return _out(_get(db, org_id, b.id))


@router.get("/{booking_id}")
def get_booking(booking_id: int, user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    return _out(_get(db, _tenant(user), booking_id))


@router.patch("/{booking_id}")
def update_booking(booking_id: int, payload: BookingPatch, user: User = Depends(permission_required("booking.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft bookings can be edited; a confirmed booking is locked")
    changes = payload.model_dump(exclude_unset=True)
    if "customer_id" in changes:
        _check_customer(db, org_id, changes["customer_id"])
    nullable = {"customer_id", "shipment_type", "booking_type", "hbl_type", "scac", "carrier", "carrier_booking_no", "vessel", "pol", "pod", "etd", "eta", "si_cutoff", "vgm_cutoff"}
    for key, value in changes.items():
        if key == "data":
            if value is not None:
                b.data = _clean(value)
        elif value is not None or key in nullable:
            setattr(b, key, value)
    _check_dates(b)
    record_audit(db, "BOOKING_UPDATED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"fields": sorted(changes)})
    db.commit()
    return _out(_get(db, org_id, booking_id))


@router.delete("/{booking_id}", status_code=204)
def delete_draft(booking_id: int, user: User = Depends(permission_required("booking.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft bookings can be deleted; cancel a confirmed booking instead")
    record_audit(db, "BOOKING_DRAFT_DELETED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"party": b.party_name})
    db.delete(b)
    db.commit()


def _num(value: Any) -> Decimal | None:
    try:
        n = Decimal(str(value).replace(",", "").strip())
        return n if n >= 0 else None
    except (InvalidOperation, ValueError):
        return None


@router.post("/{booking_id}/confirm")
def confirm_booking(booking_id: int, user: User = Depends(permission_required("booking.confirm")), db: Session = Depends(get_db)):
    """DRAFT -> CONFIRMED: assigns the booking number, creates the linked job (if none), and locks the booking."""
    org_id = _tenant(user)
    if not has_permission(user, "shipment.create"):
        raise HTTPException(status_code=403, detail="Permission denied: confirming a booking creates the job and needs shipment.create")
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft bookings can be confirmed")
    missing = [label for label, value in (("customer", b.party_name), ("carrier", b.carrier), ("port of loading", b.pol), ("port of discharge", b.pod)) if not value]
    if missing:
        raise HTTPException(status_code=422, detail="Required before confirming: " + ", ".join(missing))
    from .shipments_api import _next_job_number

    if not b.job_number:
        data = b.data or {}
        pcs, gwt = _num(data.get("pcs")), _num(data.get("gwt"))
        job = Shipment(organization_id=org_id, job_number=_next_job_number(db, org_id, b.mode, b.direction), mode=b.mode, direction=b.direction, container_mode=b.shipment_type if b.shipment_type in ("FCL", "LCL") else None,
                       pol=b.pol, pod=b.pod, etd=b.etd, eta=b.eta, customer_id=b.customer_id, consignee=(data.get("consignee") or None), packages=int(pcs) if pcs is not None and pcs == pcs.to_integral_value() else None,
                       gross_weight=gwt, commodity=(data.get("marks") or None), status="Booked", created_by=user.id)
        db.add(job)
        db.flush()
        b.job_number = job.job_number
    b.number = next_number(db, org_id, "booking", "BKG", b.booking_date)
    b.status = "CONFIRMED"
    b.confirmed_at = datetime.now(timezone.utc)
    record_audit(db, "BOOKING_CONFIRMED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"number": b.number, "job_number": b.job_number})
    db.commit()
    return _out(_get(db, org_id, booking_id))


def _close(booking_id: int, new_status: str, user: User, db: Session) -> dict:
    org_id = _tenant(user)
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "CONFIRMED":
        raise HTTPException(status_code=409, detail="Only a confirmed booking can be cancelled or have its space released")
    b.status = new_status
    b.closed_at = datetime.now(timezone.utc)
    record_audit(db, f"BOOKING_{new_status}", "OPERATIONS", user, org_id, "Booking", str(b.id), {"number": b.number})
    db.commit()
    return _out(_get(db, org_id, booking_id))


@router.post("/{booking_id}/cancel")
def cancel(booking_id: int, user: User = Depends(permission_required("booking.confirm")), db: Session = Depends(get_db)):
    return _close(booking_id, "CANCELLED", user, db)


@router.post("/{booking_id}/release-space")
def release_space(booking_id: int, user: User = Depends(permission_required("booking.confirm")), db: Session = Depends(get_db)):
    return _close(booking_id, "SPACE_RELEASED", user, db)


@router.post("/{booking_id}/duplicate", status_code=201)
def duplicate(booking_id: int, user: User = Depends(permission_required("booking.create")), db: Session = Depends(get_db)):
    """New DRAFT re-using the details of an existing booking (no number, no job, no carrier booking reference)."""
    org_id = _tenant(user)
    src = _get(db, org_id, booking_id)
    copy = Booking(organization_id=org_id, status="DRAFT", booking_date=date.today(), mode=src.mode, direction=src.direction, shipment_type=src.shipment_type, booking_type=src.booking_type, hbl_type=src.hbl_type, scac=src.scac,
                   customer_id=src.customer_id, party_name=src.party_name, carrier=src.carrier, vessel=src.vessel, pol=src.pol, pod=src.pod, data=dict(src.data or {}), created_by=user.id)
    db.add(copy)
    db.flush()
    record_audit(db, "BOOKING_DUPLICATED", "OPERATIONS", user, org_id, "Booking", str(copy.id), {"from": src.number or src.id})
    db.commit()
    return _out(_get(db, org_id, copy.id))
