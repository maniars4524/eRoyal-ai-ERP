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
from .models import Booking, Customer, Quotation, Shipment, User
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
    quotation_id: int | None = None
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
    quotation_id: int | None = None
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


def _jobs(db: Session, b: Booking) -> list[dict]:
    rows = db.scalars(select(Shipment).where(Shipment.organization_id == b.organization_id, Shipment.booking_id == b.id).order_by(Shipment.id))
    return [{"job_number": j.job_number, "job_type": j.job_type, "status": j.status, "job_stage": j.job_stage, "created_at": j.created_at} for j in rows]


def _out(b: Booking) -> dict:
    return {"id": b.id, "number": b.number, "status": b.status, "booking_date": b.booking_date, "mode": b.mode, "direction": b.direction, "shipment_type": b.shipment_type, "booking_type": b.booking_type, "hbl_type": b.hbl_type, "scac": b.scac,
            "customer_id": b.customer_id, "party_name": b.party_name, "carrier": b.carrier, "carrier_booking_no": b.carrier_booking_no, "vessel": b.vessel, "pol": b.pol, "pod": b.pod, "etd": b.etd, "eta": b.eta,
            "si_cutoff": b.si_cutoff, "vgm_cutoff": b.vgm_cutoff, "job_number": b.job_number, "quotation_id": b.quotation_id, "data": b.data or {}, "confirmed_at": b.confirmed_at, "closed_at": b.closed_at, "created_at": b.created_at}


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


def _check_quotation(db: Session, org_id: int, quotation_id: int | None) -> None:
    if quotation_id and not db.scalar(select(Quotation.id).where(Quotation.id == quotation_id, Quotation.organization_id == org_id)):
        raise HTTPException(status_code=404, detail="Quotation not found")


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
    rows = list(db.scalars(query.order_by(Booking.booking_date.desc(), Booking.id.desc()).limit(limit)))
    by_booking: dict[int, list[str]] = {}
    if rows:
        for jn, bid in db.execute(select(Shipment.job_number, Shipment.booking_id).where(Shipment.organization_id == org_id, Shipment.booking_id.in_([r.id for r in rows])).order_by(Shipment.id)):
            by_booking.setdefault(bid, []).append(jn)
    return [{**_out(b), "jobs": by_booking.get(b.id, [])} for b in rows]


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
    _check_quotation(db, org_id, payload.quotation_id)
    values = payload.model_dump(exclude={"data", "booking_date"})
    b = Booking(organization_id=org_id, status="DRAFT", booking_date=payload.booking_date or date.today(), data=_clean(payload.data), created_by=user.id, **values)
    _check_dates(b)
    db.add(b)
    db.flush()
    b.number = next_number(db, org_id, "booking", "BKG", b.booking_date, user=user)        # the number is issued when the booking is first saved; draft / confirmed / closed keep it
    record_audit(db, "BOOKING_CREATED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"party": b.party_name})
    db.commit()
    return _out(_get(db, org_id, b.id))


@router.get("/{booking_id}")
def get_booking(booking_id: int, user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    b = _get(db, _tenant(user), booking_id)
    return {**_out(b), "jobs": _jobs(db, b)}


@router.patch("/{booking_id}")
def update_booking(booking_id: int, payload: BookingPatch, user: User = Depends(permission_required("booking.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft bookings can be edited; a confirmed booking is locked")
    changes = payload.model_dump(exclude_unset=True)
    if "customer_id" in changes:
        _check_customer(db, org_id, changes["customer_id"])
    if changes.get("quotation_id"):
        _check_quotation(db, org_id, changes["quotation_id"])
    nullable = {"customer_id", "shipment_type", "booking_type", "hbl_type", "scac", "carrier", "carrier_booking_no", "vessel", "pol", "pod", "etd", "eta", "si_cutoff", "vgm_cutoff", "quotation_id"}
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
    """DRAFT -> CONFIRMED: assigns the booking number and locks the booking. Jobs (freight, consol, CHA, transport ...) are then created from the booking, all under this one number."""
    org_id = _tenant(user)
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft bookings can be confirmed")
    missing = [label for label, value in (("customer", b.party_name), ("carrier", b.carrier), ("port of loading", b.pol), ("port of discharge", b.pod)) if not value]
    if missing:
        raise HTTPException(status_code=422, detail="Required before confirming: " + ", ".join(missing))
    if b.quotation_id:
        quote = db.get(Quotation, b.quotation_id)
        if not quote or quote.status != "ACCEPTED":
            raise HTTPException(status_code=409, detail="The linked quotation " + ((quote.number if quote else "") or "") + " is " + (quote.status if quote else "missing") + "; only an accepted quotation can be booked")
    from .credit_control import enforce as credit_enforce

    credit_enforce(db, org_id, b.customer_id, "confirm booking", user.id, None, Decimal(0))
    if not b.number:        # normally issued when the draft was first saved
        b.number = next_number(db, org_id, "booking", "BKG", b.booking_date, user=user)
    b.status = "CONFIRMED"
    b.confirmed_at = datetime.now(timezone.utc)
    record_audit(db, "BOOKING_CONFIRMED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"number": b.number})
    created: list[str] = []
    if has_permission(user, "shipment.create"):
        booked = _booked_services(b)
        for service in ("Freight", "CHA"):
            if service in booked:
                created.append(_make_job(db, org_id, b, service, user).job_number)
    db.commit()
    return {**_out(_get(db, org_id, booking_id)), "jobs": _jobs(db, _get(db, org_id, booking_id)), "created_jobs": created}


SERVICE_JOB_TYPES = {"Freight": "Forwarding", "Consol": "Consol", "CHA": "Customs", "Transport": "Transport", "Warehouse": "Warehouse"}
MODULE_SERVICE = {"FOR": "Freight", "CUS": "CHA", "TRP": "Transport", "WHS": "Warehouse", "ALL": "Freight"}


class JobFromBooking(BaseModel):
    service: str = Field(pattern="^(Freight|Consol|CHA|Transport|Warehouse)$")


def _booked_services(b: Booking) -> list[str]:
    raw = str((b.data or {}).get("services") or "Freight")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _merge_booking_rows(rows: list[dict]) -> list[dict]:
    """Costing rows of the same charge (e.g. Ocean Freight - 20GP and Ocean Freight - 40HC) become ONE head on the job: one row for the total, the container break-up kept in its description so the invoice shows it."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        name = (r.get("description") or r.get("code") or "").strip()
        base = name.split(" — ")[0].strip()
        groups.setdefault((base.lower(), r.get("type"), (r.get("currency") or "INR")[:3], str(_dec(r.get("exch"), "1")), str(_dec(r.get("tax_pct"))), (r.get("vendor") or "").strip().lower(), r.get("service") or "Freight"), []).append(r)
    out = []
    for rs in groups.values():
        first = rs[0]
        base = (first.get("description") or first.get("code") or "").strip().split(" — ")[0].strip()
        if len(rs) == 1:
            out.append({**first, "_base": base})
            continue
        parts = []
        total = Decimal(0)
        for r in rs:
            qty, rate = _dec(r.get("qty"), "1") or Decimal(1), _dec(r.get("rate"))
            total += qty * rate
            suffix = (r.get("description") or "").strip().split(" — ", 1)
            parts.append({"type": suffix[1].strip() if len(suffix) > 1 else "", "qty": qty, "rate": rate})
        out.append({**first, "description": base, "qty": "1", "rate": str(total), "uom": "Per Container", "_base": base, "_parts": parts})
    return out


def _charges_from_booking(db: Session, org_id: int, job: Shipment, rows: list[dict], client: str, service: str = "Freight") -> Decimal:
    """The booking's costing rows become the job's charge lines: AR rows are the sell side, AP rows the buy side (with the vendor chosen on the booking). HSN / SAC comes from the Charge Master."""
    from .charge_utils import add_side, charge_info, hsn_for
    from .models import JobCharge

    info = charge_info(db, org_id)
    lines: list[JobCharge] = []
    revenue = Decimal(0)
    for r in _merge_booking_rows(rows):
        name = (r.get("description") or r.get("code") or "").strip()
        if not name:
            continue
        cname = (r.get("_base") or name)[:160]
        hsn = hsn_for(info, cname)
        qty, rate, exch = _dec(r.get("qty"), "1") or Decimal(1), _dec(r.get("rate")), _dec(r.get("exch"), "1") or Decimal(1)
        ccy = (r.get("currency") or "INR")[:3]
        if ccy == "INR":
            exch = Decimal(1)
        tax = _dec(r.get("tax_pct"))
        taxtype = "Taxable" if tax > 0 else "Non-Taxable"
        if r.get("type") == "AP":
            match = next((l for l in lines if l.charge_name.strip().lower() == cname.lower() and not l.buy_rate), None)
            fields = dict(buy_ccy=ccy, buy_exch=exch, buy_rate=rate, buy_tax_type=taxtype, buy_tax_pct=tax, vendor=(r.get("vendor") or "")[:160], use_vendor=bool(r.get("vendor")))
            if match is not None:
                for k, v in fields.items():
                    setattr(match, k, v)
                if r.get("_parts"):
                    match.breakup = add_side(match.breakup, "buy", r["_parts"])
            else:
                lines.append(JobCharge(organization_id=org_id, shipment_id=job.id, service=service, charge_name=cname, description=name[:240], uom=(r.get("uom") or "Per Shipment")[:40], qty=qty, hsn=hsn, sell_rate=Decimal(0),
                                       breakup=add_side(None, "buy", r["_parts"]) if r.get("_parts") else None, **fields))
        else:
            lines.append(JobCharge(organization_id=org_id, shipment_id=job.id, service=service, charge_name=cname, description=name[:240], uom=(r.get("uom") or "Per Shipment")[:40], qty=qty, hsn=hsn, sell_ccy=ccy, sell_exch=exch, sell_rate=rate,
                                   sell_tax_type=taxtype, sell_tax_pct=tax, client=client, use_client=True, breakup=add_side(None, "sell", r["_parts"]) if r.get("_parts") else None))
            revenue += (qty * rate * exch).quantize(Decimal("0.01"))
    for i, l in enumerate(lines, start=1):
        l.line_no = i
        db.add(l)
    return revenue


@router.get("/{booking_id}/jobs")
def booking_jobs(booking_id: int, user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    return _jobs(db, _get(db, _tenant(user), booking_id))


def _booking_salesperson(db: Session, b: Booking) -> str | None:
    """Who sold the shipment: the sales person named on the booking, else the one on the quotation the booking came from."""
    named = str((b.data or {}).get("sales_person") or "").strip()
    if named:
        return named[:120]
    if b.quotation_id:
        q = db.get(Quotation, b.quotation_id)
        sp = str(((q.header if q else None) or {}).get("sales_person") or "").strip()
        return sp[:120] or None
    return None


def _make_job(db: Session, org_id: int, b: Booking, service: str, user: User) -> Shipment:
    """Create one job of a confirmed booking (all booking details carried over, costing rows of that service become its charges)."""
    needed = "Freight" if service == "Consol" else service
    job_type = SERVICE_JOB_TYPES[service]
    from .shipments_api import _next_job_number

    data = b.data or {}
    pcs, gwt = _num(data.get("pcs")), _num(data.get("gwt"))
    container_mode = "LCL" if service == "Consol" else (b.shipment_type if b.shipment_type in ("FCL", "LCL") else None)
    job = Shipment(organization_id=org_id, job_number=_next_job_number(db, org_id, b.mode, b.direction, container_mode, user), job_type=job_type, booking_id=b.id, mode=b.mode, direction=b.direction, container_mode=container_mode,
                   pol=b.pol, pod=b.pod, etd=b.etd, eta=b.eta, customer_id=b.customer_id, consignee=(data.get("consignee") or None), packages=int(pcs) if pcs is not None and pcs == pcs.to_integral_value() else None,
                   gross_weight=gwt, volume=_num(data.get("volume")), package_type=(data.get("pack_type") or None), commodity=(data.get("commodity") or data.get("marks") or None), status="Booked", created_by=user.id,
                   branch=(str(data.get("branch") or "").strip() or None), salesperson=_booking_salesperson(db, b))
    if job_type == "Customs":
        job.customs_status = "CREATED"
        job.parent_job_id = db.scalar(select(Shipment.id).where(Shipment.organization_id == org_id, Shipment.booking_id == b.id, Shipment.job_type == "Forwarding").order_by(Shipment.id))
    db.add(job)
    db.flush()
    if job_type == "Forwarding":          # CHA jobs made before the freight job now point at it explicitly
        for orphan in db.scalars(select(Shipment).where(Shipment.organization_id == org_id, Shipment.booking_id == b.id, Shipment.job_type == "Customs", Shipment.parent_job_id.is_(None))):
            orphan.parent_job_id = job.id
    first = not _jobs_exist(db, b, exclude=job.id)
    from . import job_lifecycle as jl

    jl.on_job_created(db, job, user, "booking " + (b.number or ""), {"booking": b.number, "service": service})
    rows = [r for r in (data.get("charges") or []) if (r.get("service") or "Freight") == needed]
    if rows:
        job.revenue = _charges_from_booking(db, org_id, job, rows, b.party_name, needed)
    elif b.quotation_id and first and not (data.get("charges") or []):
        from .quotations_api import copy_quote_charges

        copy_quote_charges(db, org_id, b.quotation_id, job, b.carrier)
    if b.quotation_id and first:
        from .quotations_api import mark_converted_by_booking

        mark_converted_by_booking(db, org_id, b.quotation_id, job, user)
    if first:
        b.job_number = job.job_number
    record_audit(db, "BOOKING_JOB_CREATED", "OPERATIONS", user, org_id, "Booking", str(b.id), {"number": b.number, "job_number": job.job_number, "service": service})
    return job


@router.post("/{booking_id}/jobs", status_code=201)
def create_job_from_booking(booking_id: int, payload: JobFromBooking, user: User = Depends(permission_required("booking.confirm")), db: Session = Depends(get_db)):
    """Freight and CHA jobs are created automatically when the booking is released; this creates any other service (consol, transport, warehouse) or one that was missed."""
    org_id = _tenant(user)
    if not has_permission(user, "shipment.create"):
        raise HTTPException(status_code=403, detail="Permission denied: creating a job needs shipment.create")
    b = _get(db, org_id, booking_id, lock=True)
    if b.status != "CONFIRMED":
        raise HTTPException(status_code=409, detail="Jobs can be created only from a confirmed booking")
    booked = _booked_services(b)
    needed = "Freight" if payload.service == "Consol" else payload.service
    if needed not in booked:
        raise HTTPException(status_code=422, detail=payload.service + " was not booked on this booking (booked: " + ", ".join(booked) + ")")
    job_type = SERVICE_JOB_TYPES[payload.service]
    if db.scalar(select(Shipment.id).where(Shipment.organization_id == org_id, Shipment.booking_id == b.id, Shipment.job_type == job_type)):
        raise HTTPException(status_code=409, detail="A " + payload.service.lower() + " job already exists for booking " + (b.number or ""))
    job = _make_job(db, org_id, b, payload.service, user)
    db.commit()
    return {**_out(_get(db, org_id, booking_id)), "jobs": _jobs(db, _get(db, org_id, booking_id)), "created_job": job.job_number}


@router.get("/{booking_id}/costing")
def booking_costing(booking_id: int, user: User = Depends(permission_required("booking.read")), db: Session = Depends(get_db)):
    """Freight and customs clearance charges of every job under the booking: separately by service, and combined."""
    from .job_charges_api import line_out
    from .models import JobCharge

    org_id = _tenant(user)
    b = _get(db, org_id, booking_id)
    out = {"booking_number": b.number, "services": {}, "lines": [], "combined": {"sell": "0", "buy": "0", "margin": "0"}}
    tot = {"sell": Decimal(0), "buy": Decimal(0)}
    for job in db.scalars(select(Shipment).where(Shipment.organization_id == org_id, Shipment.booking_id == b.id).order_by(Shipment.id)):
        for row in db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == org_id).order_by(JobCharge.line_no)):
            ln = line_out(row)
            sell, buy = Decimal(str(ln["_sell"]["inr"])), Decimal(str(ln["_buy"]["inr"]))
            svc = out["services"].setdefault(row.service or "Freight", {"sell": Decimal(0), "buy": Decimal(0), "lines": 0})
            svc["sell"] += sell
            svc["buy"] += buy
            svc["lines"] += 1
            tot["sell"] += sell
            tot["buy"] += buy
            out["lines"].append({"job_number": job.job_number, "job_type": job.job_type, "service": row.service or "Freight", "charge_name": row.charge_name, "vendor": row.vendor, "sell_inr": str(sell), "buy_inr": str(buy)})
    out["services"] = {k: {"sell": str(v["sell"]), "buy": str(v["buy"]), "margin": str(v["sell"] - v["buy"]), "lines": v["lines"]} for k, v in out["services"].items()}
    out["combined"] = {"sell": str(tot["sell"]), "buy": str(tot["buy"]), "margin": str(tot["sell"] - tot["buy"])}
    return out


def _jobs_exist(db: Session, b: Booking, exclude: int) -> bool:
    return bool(db.scalar(select(Shipment.id).where(Shipment.organization_id == b.organization_id, Shipment.booking_id == b.id, Shipment.id != exclude)))


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
    copy.number = next_number(db, org_id, "booking", "BKG", copy.booking_date, user=user)
    record_audit(db, "BOOKING_DUPLICATED", "OPERATIONS", user, org_id, "Booking", str(copy.id), {"from": src.number or src.id})
    db.commit()
    return _out(_get(db, org_id, copy.id))
