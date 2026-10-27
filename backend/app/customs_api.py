"""Customs clearance: Shipping Bills (export) and Bills of Entry (import). Several bills can sit under one booking; they show in the Custom section of every job of that booking."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Booking, CustomsBill, JobCharge, Shipment, User

router = APIRouter(prefix="/api/customs", tags=["customs clearance"])


class BillIn(BaseModel):
    bill_no: str = Field(min_length=1, max_length=40)
    bill_date: date | None = None
    reference: str | None = Field(default=None, max_length=120)
    port_code: str | None = Field(default=None, max_length=20)
    assessable_value: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=2)
    duty_amount: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=2)
    examination: str | None = Field(default=None, max_length=40)
    ooc_date: date | None = None
    status: str = Field(default="Filed", pattern="^(Draft|Filed|Assessed|Cleared|Cancelled)$")
    remarks: str | None = Field(default=None, max_length=2000)


class BillPatch(BaseModel):
    bill_date: date | None = None
    reference: str | None = Field(default=None, max_length=120)
    port_code: str | None = Field(default=None, max_length=20)
    assessable_value: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    duty_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    examination: str | None = Field(default=None, max_length=40)
    ooc_date: date | None = None
    status: str | None = Field(default=None, pattern="^(Draft|Filed|Assessed|Cleared|Cancelled)$")
    remarks: str | None = Field(default=None, max_length=2000)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Customs clearance is tenant-scoped")
    return user.organization_id


def _job(db: Session, org_id: int, job_number: str) -> Shipment:
    job = db.scalar(select(Shipment).where(Shipment.organization_id == org_id, Shipment.job_number == job_number))
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def bill_type_for(job: Shipment) -> str:
    if job.direction == "Export":
        return "SB"
    if job.direction == "Import":
        return "BE"
    raise HTTPException(422, "Customs clearance bills apply to export (Shipping Bill) and import (Bill of Entry) jobs")


def _out(b: CustomsBill, jobs: dict[int, Shipment]) -> dict:
    j = jobs.get(b.shipment_id)
    return {"id": b.id, "bill_type": b.bill_type, "bill_no": b.bill_no, "bill_date": b.bill_date, "reference": b.reference, "port_code": b.port_code, "assessable_value": str(b.assessable_value), "duty_amount": str(b.duty_amount),
            "examination": b.examination, "ooc_date": b.ooc_date, "status": b.status, "remarks": b.remarks, "entered_in_job": j.job_number if j else None, "entered_in_type": j.job_type if j else None, "created_at": b.created_at}


def job_bills(db: Session, job: Shipment) -> list[CustomsBill]:
    """Every bill of the job and of its booking: the freight job shows what the CHA job processed."""
    cond = [CustomsBill.shipment_id == job.id]
    if job.booking_id:
        cond.append(CustomsBill.booking_id == job.booking_id)
    return list(db.scalars(select(CustomsBill).where(CustomsBill.organization_id == job.organization_id, or_(*cond)).order_by(CustomsBill.id)))


def summary(db: Session, job: Shipment) -> dict:
    bills = [b for b in job_bills(db, job) if b.status != "Cancelled"]
    kind = "SB" if job.direction == "Export" else "BE" if job.direction == "Import" else None
    jobs = {j.id: j for j in db.scalars(select(Shipment).where(Shipment.id.in_({b.shipment_id for b in bills}))) } if bills else {}
    first = bills[0] if bills else None
    cha = db.scalars(select(Shipment).where(Shipment.organization_id == job.organization_id, Shipment.booking_id == job.booking_id, Shipment.job_type == "Customs")).all() if job.booking_id else []
    charges = []
    for cj in cha:
        for c in db.scalars(select(JobCharge).where(JobCharge.shipment_id == cj.id)):
            charges.append({"job_number": cj.job_number, "charge_name": c.charge_name, "sell_rate": str(c.sell_rate), "buy_rate": str(c.buy_rate), "vendor": c.vendor, "qty": str(c.qty)})
    booking = db.get(Booking, job.booking_id) if job.booking_id else None
    return {"job_number": job.job_number, "bill_type": kind, "label": "Shipping Bill" if kind == "SB" else "Bill of Entry" if kind == "BE" else None, "count": len(bills), "primary_no": first.bill_no if first else None, "primary_date": first.bill_date if first else None,
            "booking_number": booking.number if booking else None, "bills": [_out(b, jobs) for b in job_bills(db, job)], "customs_jobs": [{"job_number": c.job_number, "stage": c.job_stage} for c in cha], "customs_charges": charges}


@router.get("/by-job/{job_number:path}")
def list_for_job(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    return summary(db, _job(db, org_id, job_number))


@router.post("/by-job/{job_number:path}", status_code=201)
def add_bill(job_number: str, body: BillIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    from . import job_lifecycle as jl

    org_id = _tenant(user)
    job = _job(db, org_id, job_number)
    jl.assert_job_editable(job, "customs clearance bills")
    kind = bill_type_for(job)
    bill = CustomsBill(organization_id=org_id, booking_id=job.booking_id, shipment_id=job.id, bill_type=kind, bill_no=body.bill_no.strip(), created_by=user.id, **body.model_dump(exclude={"bill_no"}))
    db.add(bill)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"{'Shipping Bill' if kind == 'SB' else 'Bill of Entry'} {body.bill_no} is already recorded")
    jl.log_event(db, job, user, "CUSTOMS_BILL_ADDED", "OPERATIONS", f"{'Shipping Bill' if kind == 'SB' else 'Bill of Entry'} {bill.bill_no} added", new=bill.bill_no, related_type="CUSTOMS_BILL", related_id=str(bill.id))
    record_audit(db, "CUSTOMS_BILL_ADDED", "OPERATIONS", user, org_id, "CustomsBill", str(bill.id), {"job": job.job_number, "bill_no": bill.bill_no, "type": kind})
    db.commit()
    return summary(db, job)


@router.patch("/{bill_id}")
def update_bill(bill_id: int, body: BillPatch, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    from . import job_lifecycle as jl

    org_id = _tenant(user)
    bill = db.scalar(select(CustomsBill).where(CustomsBill.id == bill_id, CustomsBill.organization_id == org_id))
    if not bill:
        raise HTTPException(404, "Customs bill not found")
    job = db.get(Shipment, bill.shipment_id)
    jl.assert_job_editable(job, "customs clearance bills")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(bill, k, v)
    jl.log_event(db, job, user, "CUSTOMS_BILL_UPDATED", "OPERATIONS", f"Customs bill {bill.bill_no} updated", new=bill.status, related_type="CUSTOMS_BILL", related_id=str(bill.id), detail={"fields": sorted(changes)})
    record_audit(db, "CUSTOMS_BILL_UPDATED", "OPERATIONS", user, org_id, "CustomsBill", str(bill.id), {"fields": sorted(changes)})
    db.commit()
    return summary(db, job)
