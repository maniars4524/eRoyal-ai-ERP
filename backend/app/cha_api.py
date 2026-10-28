"""CHA (customs house agent) jobs. One booking has one freight job and one or MANY CHA jobs; every CHA job points at its freight job (`parent_job_id`), has its own number, its own Shipping Bill / Bill of Entry
records and its own customs status. The freight job shows all of them in its CUSTOMS / CHA JOB INFORMATION section; nothing is merged and a booking number is never made unique."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import job_lifecycle as jl
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .job_details_api import _row, location_code
from . import cha_spec
from .models import Booking, CustomsBill, Customer, JobCharge, JobDetail, Shipment, User

router = APIRouter(tags=["cha jobs"])

CHA_STATUSES = ["CREATED", "DOCUMENTATION", "DATA ENTRY", "VALIDATED", "CHECKLIST", "CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION", "OOC / LEO CLEARED", "DELIVERED", "CLOSED"]
FILING_PENDING = {"CREATED", "DOCUMENTATION", "DATA ENTRY", "VALIDATED", "CHECKLIST"}          # not yet filed with customs
DONE = {"DELIVERED", "CLOSED"}
# dashboard cards: Draft = being prepared, Pending = ready to file, Filed = with customs, Cleared = out of charge / let export order
GROUPS = {"draft": {"CREATED", "DOCUMENTATION", "DATA ENTRY"}, "pending": {"VALIDATED", "CHECKLIST"}, "filed": {"CUSTOMS FILED", "CUSTOMS RESPONSE"}, "sent": {"CUSTOMS FILED"}, "received": {"CUSTOMS RESPONSE"}, "examination": {"EXAMINATION"}, "cleared": {"OOC / LEO CLEARED"},
          "completed": {"DELIVERED", "CLOSED"}}


def is_delayed(j: Shipment, today: date | None = None) -> bool:
    """Past its sailing / arrival date without reaching the stage it should have: an export not filed by its ETD, an import not cleared by its ETA."""
    today = today or date.today()
    st = j.customs_status or "CREATED"
    if st in DONE:
        return False
    if j.direction == "Export":
        return bool(j.etd and j.etd < today and st in FILING_PENDING)
    return bool(j.eta and j.eta < today and st != "OOC / LEO CLEARED")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "CHA jobs are company data")
    mods = user.organization.modules if user.organization else None
    if mods is not None and "customs" not in mods:
        raise HTTPException(403, "The Customs / CHA module is not enabled in this company's licence")
    return user.organization_id


def _norm(t: str | None) -> str:
    return " ".join(str(t or "").upper().split())


def parties_of(db: Session, job: Shipment) -> tuple[str, str]:
    """(exporter, importer) of a job: the shipper and the consignee saved on the job, else the customer / consignee of the job record."""
    row = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    p = (row.parties if row else None) or {}
    info = job.cha_info or {}
    if info.get("exporter") or info.get("importer"):
        return str(info.get("exporter") or "").strip(), str(info.get("importer") or "").strip()
    shipper = str(p.get("shipper") or "").strip()
    if not shipper or shipper.startswith("__"):
        c = db.get(Customer, job.customer_id) if job.customer_id else None
        shipper = c.name if c else ""
    cons = str(p.get("consignee") or "").strip()
    if not cons or cons.startswith("__"):
        cons = job.consignee or ""
    if (not shipper or not cons) and job.booking_id:          # nothing saved on the job yet: the booking's parties
        b = db.get(Booking, job.booking_id)
        if b:
            shipper = shipper or (b.party_name or "")
            cons = cons or str((b.data or {}).get("consignee") or "")
    return shipper, cons


def documents_for(db: Session, freight: Shipment | None, cha: Shipment) -> list[dict]:
    """The customs documents of this kind of job: Export Air = AWB + Air Manifest Declaration, Export Sea = BL, Import = Cargo Arrival Notice + Delivery Order. They belong to the freight job's records."""
    src = freight or cha
    row = db.scalar(select(JobDetail).where(JobDetail.shipment_id == src.id))
    bl = (row.bl if row else None) or {}
    saved = (row.saved_sections if row else None) or {}
    cinfo = cha.cha_info or {}
    ref = bl.get("hblNo") or bl.get("directNo") or bl.get("mblNo") or cinfo.get("hbl") or cinfo.get("mbl") or cha.bl_awb or ""        # the freight job's BL / AWB, else the number entered on the CHA job itself (export: CHA comes first)
    out: list[dict] = []
    if cha.direction == "Export" and cha.mode == "Air":
        out = [{"key": "awb", "name": "Air Waybill (AWB)", "number": ref, "available": bool(ref)}, {"key": "manifest", "name": "Air Manifest Declaration", "number": ref, "available": bool(ref)}]
    elif cha.direction == "Export" and cha.mode == "Sea":
        out = [{"key": "bl", "name": "Bill of Lading (BL)", "number": ref, "available": bool(ref)}]
    elif cha.direction == "Import":
        ok = bool(saved.get("impdocs") or ref)
        out = [{"key": "can", "name": "Cargo Arrival Notice (CAN)", "number": src.job_number, "available": ok}, {"key": "do", "name": "Delivery Order (DO)", "number": src.job_number, "available": ok}]
    for d in out:
        d["job_number"] = src.job_number
    return out


def cha_row(db: Session, cha: Shipment, freight: Shipment | None = None, booking: Booking | None = None) -> dict:
    exporter, importer = parties_of(db, cha)
    bills = list(db.scalars(select(CustomsBill).where(CustomsBill.shipment_id == cha.id, CustomsBill.organization_id == cha.organization_id).order_by(CustomsBill.id)))
    live = [b for b in bills if b.status != "Cancelled"]
    from .job_charges_api import compute_side

    cost = sell = 0.0
    for c in db.scalars(select(JobCharge).where(JobCharge.shipment_id == cha.id, JobCharge.organization_id == cha.organization_id)):
        cost += float(compute_side(c.qty, c.buy_ccy, c.buy_exch, c.buy_rate, c.buy_tax_type, c.buy_tax_pct)["inr"])
        sell += float(compute_side(c.qty, c.sell_ccy, c.sell_exch, c.sell_rate, c.sell_tax_type, c.sell_tax_pct)["inr"])
    export = cha.direction == "Export"
    first = live[0] if live else None
    return {"id": cha.id, "cha_job_number": cha.job_number, "booking_number": booking.number if booking else None, "booking_id": cha.booking_id, "freight_job_number": freight.job_number if freight else None,
            "exporter": exporter, "importer": importer, "pol": cha.pol, "pod": cha.pod, "shipment_type": cha.direction, "shipment_mode": cha.mode,
            "shipping_bill_no": first.bill_no if (first and export) else None, "shipping_bill_date": first.bill_date if (first and export) else None,
            "bill_of_entry_no": first.bill_no if (first and not export) else None, "bill_of_entry_date": first.bill_date if (first and not export) else None,
            "bills": [{"id": b.id, "bill_type": b.bill_type, "bill_no": b.bill_no, "bill_date": b.bill_date, "status": b.status, "ooc_date": b.ooc_date, "examination": b.examination} for b in bills],
            "customs_status": cha.customs_status or "CREATED", "job_stage": cha.job_stage, "etd": cha.etd, "eta": cha.eta, "created_at": cha.created_at,
            "charges_cost": round(cost, 2), "charges_sell": round(sell, 2), "documents": documents_for(db, freight, cha), "linked_by": "parent" if cha.parent_job_id else "booking"}


def freight_of(db: Session, job: Shipment) -> Shipment | None:
    """The freight job a CHA job belongs to (its explicit parent, else the freight job of its booking)."""
    if job.job_type != "Customs":
        return job
    if job.parent_job_id:
        return db.get(Shipment, job.parent_job_id)
    if job.booking_id:
        return db.scalar(select(Shipment).where(Shipment.organization_id == job.organization_id, Shipment.booking_id == job.booking_id, Shipment.job_type == "Forwarding").order_by(Shipment.id))
    return None


def cha_jobs_of(db: Session, freight: Shipment) -> list[Shipment]:
    """Every CHA job of a freight job: the ones pointing at it, plus older ones of the same booking that have no explicit parent yet."""
    cond = [Shipment.parent_job_id == freight.id]
    if freight.booking_id:
        cond.append((Shipment.booking_id == freight.booking_id) & Shipment.parent_job_id.is_(None))
    return list(db.scalars(select(Shipment).where(Shipment.organization_id == freight.organization_id, Shipment.job_type == "Customs", or_(*cond), Shipment.id != freight.id).order_by(Shipment.id)))


def consolidated(db: Session, any_job: Shipment) -> dict:
    freight = freight_of(db, any_job)
    if freight is None:
        raise HTTPException(404, "This CHA job has no freight job")
    booking = db.get(Booking, freight.booking_id) if freight.booking_id else None
    jobs = cha_jobs_of(db, freight)
    ex, im = parties_of(db, freight)
    return {"freight_job_number": freight.job_number, "freight_job_id": freight.id, "booking_number": booking.number if booking else None, "count": len(jobs), "workflow": workflow_of(freight, jobs),
            "freight": {"exporter": ex, "importer": im, "pol": freight.pol, "pod": freight.pod, "shipment_type": freight.direction, "shipment_mode": freight.mode}, "cha_jobs": [cha_row(db, j, freight, booking) for j in jobs]}


def _job(db: Session, org: int, job_number: str) -> Shipment:
    j = db.scalar(select(Shipment).where(Shipment.organization_id == org, Shipment.job_number == job_number))
    if not j:
        raise HTTPException(404, "Job not found")
    return j


@router.get("/api/jobs/{job_number:path}/cha-jobs")
def list_cha_jobs(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    return consolidated(db, _job(db, _tenant(user), job_number))


class ChaIn(BaseModel):
    exporter: str | None = Field(default=None, max_length=200)
    importer: str | None = Field(default=None, max_length=200)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    exception_reason: str = Field(default="", max_length=300)


def _add_cha(db: Session, user: User, org: int, ref: Shipment, body: ChaIn, parent: Shipment | None) -> tuple[Shipment, list[str]]:
    """One more CHA job of the booking of `ref` (the freight job, or the first CHA job of a CHA-only booking). Its exporter, importer, port of loading and port of discharge must match `ref`; an authorised user can record
    an exception with a reason. Nothing else is created: the booking number is shared and there is still one freight job."""
    from .shipments_api import _next_cha_number

    jl.assert_job_editable(ref, "its CHA jobs")
    fex, fim = parties_of(db, ref)
    ex, im = (body.exporter or fex).strip(), (body.importer or fim).strip()
    pol, pod = (body.pol or ref.pol or "").strip(), (body.pod or ref.pod or "").strip()
    wrong = []
    if _norm(ex) != _norm(fex):
        wrong.append(f"Exporter ({ex or 'blank'}) differs from the booking ({fex or 'blank'})")
    if _norm(im) != _norm(fim):
        wrong.append(f"Importer ({im or 'blank'}) differs from the booking ({fim or 'blank'})")
    if location_code(pol) != location_code(ref.pol):
        wrong.append(f"Port of loading ({pol or 'blank'}) differs from the booking ({ref.pol or 'blank'})")
    if location_code(pod) != location_code(ref.pod):
        wrong.append(f"Port of discharge ({pod or 'blank'}) differs from the booking ({ref.pod or 'blank'})")
    can_override = has_permission(user, "cha.exception")
    if wrong and not (can_override and len(body.exception_reason.strip()) >= 5):
        raise HTTPException(409, {"code": "CHA_MISMATCH", "message": "The CHA job does not match the booking: " + "; ".join(wrong), "mismatches": wrong, "can_override": can_override})
    cha = Shipment(organization_id=org, job_number=_next_cha_number(db, org, ref.mode, ref.direction, user), job_type="Customs", booking_id=ref.booking_id, parent_job_id=parent.id if parent else None, mode=ref.mode,
                   direction=ref.direction, container_mode=ref.container_mode, pol=pol or None, pod=pod or None, etd=ref.etd, eta=ref.eta, customer_id=ref.customer_id, consignee=im or None, packages=ref.packages,
                   gross_weight=ref.gross_weight, volume=ref.volume, package_type=ref.package_type, commodity=ref.commodity, status="Booked", created_by=user.id, branch=ref.branch, salesperson=ref.salesperson, customs_status="CREATED")
    db.add(cha)
    db.flush()
    db.add(JobDetail(organization_id=org, shipment_id=cha.id, parties={"shipper": ex, "consignee": im}, cargo={}, containers=[], dims={}, bl={}, saved_sections={"parties": datetime.now(timezone.utc).isoformat()}, version=1, updated_by=user.id))
    jl.on_job_created(db, cha, user, f"additional CHA job of {ref.job_number}", {"booking_job": ref.job_number})
    record_audit(db, "CHA_JOB_ADDED", "OPERATIONS", user, org, "Shipment", str(cha.id), {"job_number": cha.job_number, "from_job": ref.job_number, "booking_id": ref.booking_id, "exception": bool(wrong), "reason": body.exception_reason.strip() if wrong else ""})
    return cha, wrong


@router.post("/api/jobs/{job_number:path}/cha-jobs", status_code=201)
def add_cha_job(job_number: str, body: ChaIn, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    """Another CHA job under the same booking and freight job. No new freight job is created and the booking number is shared."""
    org = _tenant(user)
    freight = _job(db, org, job_number)
    if freight.job_type == "Customs":
        freight = freight_of(db, freight)
        if freight is None:
            raise HTTPException(409, "This CHA job has no freight job to add another CHA job to")
    if freight.direction not in ("Export", "Import"):
        raise HTTPException(422, "CHA jobs apply to export and import shipments")
    cha, _ = _add_cha(db, user, org, freight, body, freight)
    db.commit()
    out = consolidated(db, freight)
    out["created"] = cha.job_number
    return out


class StatusIn(BaseModel):
    status: str
    remarks: str = Field(default="", max_length=300)


@router.patch("/api/cha/jobs/{job_number:path}/status")
def set_status(job_number: str, body: StatusIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Moves a CHA job along its customs stages (Created ... Closed). Going back needs a reason; the move is written to the job's history."""
    org = _tenant(user)
    job = _job(db, org, job_number)
    if job.job_type != "Customs":
        raise HTTPException(422, "Only a CHA (customs) job has customs stages")
    new = body.status.strip().upper()
    if new not in CHA_STATUSES:
        raise HTTPException(422, "Unknown stage. Stages: " + ", ".join(CHA_STATUSES))
    old = job.customs_status or "CREATED"
    if new == old:
        return {"job_number": job.job_number, "customs_status": old}
    if CHA_STATUSES.index(new) < CHA_STATUSES.index(old) and len(body.remarks.strip()) < 3:
        raise HTTPException(422, "Give a reason for moving the job back to an earlier stage")
    if new == "CLOSED" and old not in ("DELIVERED", "OOC / LEO CLEARED"):
        raise HTTPException(409, "A CHA job is closed after it is cleared and delivered")
    jl.assert_job_editable(job, "the customs stage")
    job.customs_status = new
    job.last_activity_at = datetime.now(timezone.utc)
    jl.log_event(db, job, user, "CHA_STAGE_CHANGED", "OPERATIONS", f"Customs stage {old} to {new}", previous=old, new=new, reason=body.remarks.strip() or None)
    record_audit(db, "CHA_STAGE_CHANGED", "OPERATIONS", user, org, "Shipment", str(job.id), {"job_number": job.job_number, "from": old, "to": new})
    db.commit()
    return {"job_number": job.job_number, "customs_status": new, "stages": CHA_STATUSES}


@router.get("/api/cha/jobs")
def list_jobs(direction: str = "", mode: str = "", status: str = "", group: str = "", q: str = "", date_from: date | None = None, date_to: date | None = None, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
              user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """The CHA job register: every customs job of the company, filtered by Export / Import, Air / Sea and stage, each with its freight job and customs references."""
    org = _tenant(user)
    cond = [Shipment.organization_id == org, Shipment.job_type == "Customs"]
    if direction in ("Export", "Import"):
        cond.append(Shipment.direction == direction)
    if mode in ("Air", "Sea"):
        cond.append(Shipment.mode == mode)
    if status:
        cond.append(func.coalesce(Shipment.customs_status, "CREATED") == status)
    if group == "cancelled":
        cond.append(Shipment.status == "Cancelled")
    elif group:
        cond.append(Shipment.status != "Cancelled")
    if group in GROUPS:
        cond.append(func.coalesce(Shipment.customs_status, "CREATED").in_(sorted(GROUPS[group])))
    if date_from:
        cond.append(Shipment.created_at >= date_from)
    if date_to:
        cond.append(Shipment.created_at < date.fromordinal(date_to.toordinal() + 1))
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(Shipment.job_number.ilike(like), Shipment.bl_awb.ilike(like), Shipment.consignee.ilike(like), Shipment.pol.ilike(like), Shipment.pod.ilike(like), Shipment.commodity.ilike(like)))
    if group == "delayed":
        every = [j for j in db.scalars(select(Shipment).where(*cond).order_by(Shipment.id.desc())) if is_delayed(j)]
        total, jobs = len(every), every[offset:offset + limit]
    else:
        total = db.scalar(select(func.count()).select_from(Shipment).where(*cond)) or 0
        jobs = list(db.scalars(select(Shipment).where(*cond).order_by(Shipment.id.desc()).limit(limit).offset(offset)))
    rows = []
    for j in jobs:
        f = freight_of(db, j)
        rows.append({**cha_row(db, j, f, db.get(Booking, j.booking_id) if j.booking_id else None), "mbl_hbl": j.bl_awb})
    by_status = {s: 0 for s in CHA_STATUSES}
    for (st,) in db.execute(select(Shipment.customs_status).where(Shipment.organization_id == org, Shipment.job_type == "Customs")):
        by_status[st or "CREATED"] = by_status.get(st or "CREATED", 0) + 1
    return {"items": rows, "total": total, "statuses": CHA_STATUSES, "by_status": by_status}


@router.get("/api/cha/dashboard")
def dashboard(user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """The CHA dashboard figures: totals, pending, delayed, completed, import / export, documents and filing pending, the stage pipeline and Sea / Air x Export / Import."""
    org = _tenant(user)
    every = list(db.scalars(select(Shipment).where(Shipment.organization_id == org, Shipment.job_type == "Customs")))
    jobs = [j for j in every if j.status != "Cancelled"]          # cancelled jobs are counted on their own
    today = date.today()
    pending = [j for j in jobs if (j.customs_status or "CREATED") not in DONE]
    delayed = [j for j in pending if ((j.direction == "Export" and j.etd and j.etd < today and (j.customs_status or "CREATED") in FILING_PENDING) or (j.direction == "Import" and j.eta and j.eta < today and (j.customs_status or "CREATED") not in ("OOC / LEO CLEARED",)))]
    matrix = {}
    for mode in ("Sea", "Air"):
        for d in ("Export", "Import"):
            sub = [j for j in jobs if j.mode == mode and j.direction == d]
            matrix[f"{mode} {d}"] = {"active": sum(1 for j in sub if (j.customs_status or "CREATED") not in DONE), "closed": sum(1 for j in sub if (j.customs_status or "CREATED") in DONE), "pending_filing": sum(1 for j in sub if (j.customs_status or "CREATED") in FILING_PENDING)}
    by_status = {s: sum(1 for j in jobs if (j.customs_status or "CREATED") == s) for s in CHA_STATUSES}
    ids = [j.id for j in jobs]
    with_bill = {sid for (sid,) in db.execute(select(CustomsBill.shipment_id).where(CustomsBill.shipment_id.in_(ids), CustomsBill.status != "Cancelled"))} if ids else set()
    groups = {g: sum(1 for j in jobs if (j.customs_status or "CREATED") in st) for g, st in GROUPS.items()}
    groups["delayed"] = len(delayed)
    groups["total"] = len(jobs)
    groups["cancelled"] = len(every) - len(jobs)
    return {"groups": groups, "total": len(jobs), "pending": len(pending), "delayed": len(delayed), "completed": len(jobs) - len(pending), "import_jobs": sum(1 for j in jobs if j.direction == "Import"), "export_jobs": sum(1 for j in jobs if j.direction == "Export"),
            "pending_documents": sum(1 for j in pending if j.id not in with_bill and (j.customs_status or "CREATED") not in FILING_PENDING), "pending_customs_filing": sum(1 for j in jobs if (j.customs_status or "CREATED") in FILING_PENDING),
            "examination": by_status["EXAMINATION"], "ooc_pending_import": sum(1 for j in jobs if j.direction == "Import" and (j.customs_status or "CREATED") in ("CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION")),
            "ep_copy_pending_export": sum(1 for j in jobs if j.direction == "Export" and (j.customs_status or "CREATED") in ("CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION", "OOC / LEO CLEARED")),
            "delivery_pending": sum(1 for j in pending if (j.customs_status or "CREATED") == "OOC / LEO CLEARED"), "closure_pending": sum(1 for j in jobs if (j.customs_status or "CREATED") == "DELIVERED"),
            "matrix": matrix, "by_status": by_status, "statuses": CHA_STATUSES}


def _services(b: Booking) -> list[str]:
    return [x.strip() for x in str((b.data or {}).get("services") or "Freight").split(",") if x.strip()]


def booking_row(db: Session, b: Booking) -> dict:
    """A booking as offered to the CHA screen: its parties, route, dates and services, with the freight and CHA jobs already made from it."""
    jobs = list(db.scalars(select(Shipment).where(Shipment.organization_id == b.organization_id, Shipment.booking_id == b.id).order_by(Shipment.id)))
    d = b.data or {}
    cust = db.get(Customer, b.customer_id) if b.customer_id else None
    exporter, importer = (b.party_name or (cust.name if cust else "")), str(d.get("consignee") or "")
    freight = next((j for j in jobs if j.job_type == "Forwarding"), None)
    return {"id": b.id, "number": b.number, "date": b.booking_date, "status": b.status, "mode": b.mode, "direction": b.direction, "shipment_type": b.shipment_type, "services": _services(b), "exporter": exporter, "importer": importer, "pol": b.pol, "pod": b.pod,
            "etd": b.etd, "eta": b.eta, "carrier": b.carrier, "vessel": b.vessel, "carrier_booking_no": b.carrier_booking_no, "auto_generated": bool(d.get("auto_generated")), "freight_job_number": freight.job_number if freight else None,
            "cha_jobs": [j.job_number for j in jobs if j.job_type == "Customs"], "commodity": d.get("commodity") or d.get("marks"), "packages": d.get("pcs"), "gross_weight": d.get("gwt"), "consignee": importer,
            "combo": " + ".join(x for x in _services(b))}


@router.get("/api/cha/bookings")
def cha_bookings(direction: str = "", mode: str = "", user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Confirmed bookings that include the CHA service (CHA only, CHA + Freight, CHA + Transport ...), for the Create Job screen."""
    org = _tenant(user)
    cond = [Booking.organization_id == org, Booking.status == "CONFIRMED"]
    if direction in ("Export", "Import"):
        cond.append(Booking.direction == direction)
    if mode in ("Air", "Sea"):
        cond.append(Booking.mode == mode)
    rows = [b for b in db.scalars(select(Booking).where(*cond).order_by(Booking.id.desc()).limit(500)) if "CHA" in _services(b)]
    return {"items": [booking_row(db, b) for b in rows]}


def workflow_of(freight: Shipment, chas: list[Shipment]) -> dict:
    """Export: the CHA job comes first and the freight job follows once customs is cleared. Import: the freight job comes first and the CHA job follows."""
    done = ("OOC / LEO CLEARED", "DELIVERED", "CLOSED")
    cleared = bool(chas) and all((j.customs_status or "CREATED") in done for j in chas)
    if freight.direction == "Export":
        return {"order": "CHA first, then freight", "cha_cleared": cleared, "message": "Customs (CHA) clearance is complete; the freight job can proceed." if cleared else "Export: CHA jobs come first. The freight job follows after customs clearance."}
    return {"order": "Freight first, then CHA", "cha_cleared": cleared, "message": "Import: the freight job comes first; CHA jobs follow after arrival."}


class NewChaIn(BaseModel):
    job_type: str = Field(pattern="^(Export|Import)$")
    mode: str = Field(pattern="^(Sea|Air)$")
    exporter: str = Field(default="", max_length=200)
    importer: str = Field(default="", max_length=200)
    pol: str = Field(default="", max_length=80)
    pod: str = Field(default="", max_length=80)
    etd: date | None = None
    eta: date | None = None
    mbl: str = Field(default="", max_length=60)
    hbl: str = Field(default="", max_length=60)
    containers: str = Field(default="", max_length=300)
    file_ref: str = Field(default="", max_length=60)
    customs_location: str = Field(default="", max_length=60)
    cha_license: str = Field(default="", max_length=40)
    customer_id: int | None = None
    freight_job_number: str = Field(default="", max_length=40)
    booking_number: str = Field(default="", max_length=40)
    exception_reason: str = Field(default="", max_length=300)


@router.post("/api/cha/jobs", status_code=201)
def create_cha_job(body: NewChaIn, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    """Create a CHA job from the CHA screen (Export or Import, Air or Sea). Linked to a freight job, under the same rules as an additional CHA job, when a freight job number is given; otherwise a stand-alone customs job."""
    from .shipments_api import _next_cha_number

    org = _tenant(user)
    if body.booking_number.strip():
        from .bookings_api import _make_job

        bk = db.scalar(select(Booking).where(Booking.organization_id == org, Booking.number == body.booking_number.strip()))
        if not bk or bk.status != "CONFIRMED":
            raise HTTPException(404, "Confirmed booking not found")
        if "CHA" not in _services(bk):
            raise HTTPException(422, "CHA was not booked on this booking (booked: " + ", ".join(_services(bk)) + ")")
        if bk.direction != body.job_type or bk.mode != body.mode:
            raise HTTPException(409, f"The booking is {bk.mode} {bk.direction}; choose a {bk.mode} {bk.direction} CHA job for it")
        existing = list(db.scalars(select(Shipment).where(Shipment.organization_id == org, Shipment.booking_id == bk.id, Shipment.job_type == "Customs").order_by(Shipment.id)))
        freight = db.scalar(select(Shipment).where(Shipment.organization_id == org, Shipment.booking_id == bk.id, Shipment.job_type == "Forwarding").order_by(Shipment.id))
        if not existing:
            cha = _make_job(db, org, bk, "CHA", user)               # the first CHA job of the booking carries everything from it (parties, route, dates, CHA charges)
        else:
            cha, _ = _add_cha(db, user, org, freight or existing[0], ChaIn(exporter=body.exporter or None, importer=body.importer or None, pol=body.pol or None, pod=body.pod or None, exception_reason=body.exception_reason), freight)
        d = bk.data or {}
        cha.cha_info = {**(cha.cha_info or {}), "exporter": body.exporter or booking_row(db, bk)["exporter"], "importer": body.importer or booking_row(db, bk)["importer"], "booking_carrier": bk.carrier, "vessel": bk.vessel, "commodity": d.get("commodity") or d.get("marks")}
    elif body.freight_job_number.strip():
        freight = _job(db, org, body.freight_job_number.strip())
        if freight.direction != body.job_type or freight.mode != body.mode:
            raise HTTPException(409, f"The freight job is {freight.direction} {freight.mode}; a {body.job_type} {body.mode} CHA job cannot be linked to it")
        out = add_cha_job(freight.job_number, ChaIn(exporter=body.exporter or None, importer=body.importer or None, pol=body.pol or None, pod=body.pod or None, exception_reason=body.exception_reason), user, db)
        cha = _job(db, org, out["created"])
    else:
        if body.customer_id and not db.scalar(select(Customer.id).where(Customer.id == body.customer_id, Customer.organization_id == org)):
            raise HTTPException(404, "Organization not found")
        cha = Shipment(organization_id=org, job_number=_next_cha_number(db, org, body.mode, body.job_type, user), job_type="Customs", mode=body.mode, direction=body.job_type, pol=body.pol or None, pod=body.pod or None,
                       etd=body.etd, eta=body.eta, customer_id=body.customer_id, consignee=body.importer or None, status="Booked", created_by=user.id, customs_status="CREATED")
        db.add(cha)
        db.flush()
        jl.on_job_created(db, cha, user, "CHA screen", {"standalone": True})
        from .bookings_api import auto_booking

        auto_booking(db, org, user, cha, "CHA")          # a direct CHA job gets a system generated booking number
        record_audit(db, "CHA_JOB_CREATED", "OPERATIONS", user, org, "Shipment", str(cha.id), {"job_number": cha.job_number, "type": body.job_type, "mode": body.mode})
    cha.etd = body.etd or cha.etd
    cha.eta = body.eta or cha.eta
    given = {"exporter": body.exporter, "importer": body.importer, "mbl": body.mbl, "hbl": body.hbl, "containers": body.containers, "file_ref": body.file_ref, "customs_location": body.customs_location, "cha_license": body.cha_license}
    cha.cha_info = {**(cha.cha_info or {}), **{k: v for k, v in given.items() if v}}
    if body.hbl or body.mbl:
        cha.bl_awb = body.hbl or body.mbl
    db.commit()
    return get_cha_job(cha.job_number, user, db)


def party_details(db: Session, org: int, name: str | None) -> dict:
    """What the Organization master holds for an exporter / importer: IEC, AD code and bank, unit kind (General / SEZ ...), approval and LUT numbers, GSTIN and PAN, default CHA."""
    n = _norm(name)
    if not n:
        return {}
    for c in db.scalars(select(Customer).where(Customer.organization_id == org, func.upper(Customer.name) == n)):
        d = c.data or {}
        pick = lambda *keys: next((str(d[k]) for k in keys if d.get(k)), None)
        return {"name": c.name, "code": c.code, "iec": pick("iec_code", "org_iec", "iec"), "ad_code": pick("exp_ad_code"), "ad_bank": pick("exp_ad_bank"), "unit_kind": pick("trade_unit_kind"), "approval_no": pick("trade_approval_no"),
                "lut_no": pick("exp_lut_no"), "lut_valid_to": pick("exp_lut_valid_to"), "gstin": c.gstin, "pan": c.pan, "default_cha": pick("exp_default_cha", "imp_default_cha"), "rodtep": pick("exp_rodtep")}
    return {}


# ---------------------------------------------------------------- filing form (General tab of the Bill of Entry / Shipping Bill), modelled on the customs filing software
def _o(*pairs: str) -> list[list[str]]:
    return [[p.split(" - ", 1)[0], p] for p in pairs]


TAX_TYPES = _o("V - VAT", "C - CST", "S - ST", "G - GST-IN", "A - Aadhar", "P - Passport", "I - Income Tax PAN", "T - Tin No", "O - GST Govt.")
IMPORT_FORM = [
    {"title": "Importer", "fields": [
        {"k": "importer_name", "l": "Importer Name", "req": 1, "span": 2}, {"k": "iec", "l": "IEC Code", "req": 1}, {"k": "branch_sr_no", "l": "Branch Sr. No.", "req": 1, "ph": "e.g. 0 - CHENNAI"}]},
    {"title": "Importer Address", "fields": [
        {"k": "addr1", "l": "Address 1", "req": 1}, {"k": "addr2", "l": "Address 2"}, {"k": "addr3", "l": "Address 3 / City"}, {"k": "state", "l": "State"}, {"k": "pin", "l": "PIN"}]},
    {"title": "Clearance details", "fields": [
        {"k": "ad_code", "l": "Authorized Dealer Code", "req": 1}, {"k": "country_origin", "l": "Country of Origin", "req": 1, "ph": "e.g. SG - SINGAPORE"}, {"k": "country_consignment", "l": "Country of Consignment", "req": 1, "ph": "e.g. SG - SINGAPORE"},
        {"k": "port_origin", "l": "Port of Origin", "req": 1, "ph": "e.g. SGSIN"}, {"k": "port_shipment", "l": "Port of Shipment", "req": 1, "ph": "e.g. SGSIN"},
        {"k": "payment_method", "l": "Payment Method Code", "req": 1, "t": "select", "o": _o("T - Transactional", "D - Deferred")},
        {"k": "be_type", "l": "BE Type", "req": 1, "t": "select", "o": _o("H - Home Consumption", "W - Warehouse", "X - Ex-bond", "Z - SEZ (FTA->SEZ)", "T - SEZ (DTA Sales - Trading)", "M - SEZ (DTA Sales - Manufacturing)", "V - SEZ Goods Movement", "S - SEZ (DTA Sales - Stock Clearance)")},
        {"k": "importer_type", "l": "Importer Exporter Type", "ph": "e.g. P - Private"}, {"k": "mode_of_transport", "l": "Mode of Transport", "req": 1, "t": "select", "o": _o("L - Land", "S - Sea", "A - Air")},
        {"k": "filing_status", "l": "Filing Status", "req": 1, "t": "select", "o": _o("N - Normal", "Y - Prior", "A - Advanced")}, {"k": "state_of_origin", "l": "State of Origin"},
        {"k": "commercial_tax_type", "l": "Commercial Tax Type", "req": 1, "t": "select", "o": TAX_TYPES}, {"k": "commercial_tax_reg_no", "l": "Commercial Tax Registration No."}]},
    {"title": "Warehouse / Ex-bond", "fields": [
        {"k": "warehouse", "l": "Warehouse"}, {"k": "customs_location", "l": "Customs Location"}, {"k": "wh_be_no", "l": "Warehouse BE No."}, {"k": "wh_be_date", "l": "Warehouse BE Date", "t": "date"},
        {"k": "packages_released", "l": "No. of Packages Released (Ex-bond)"}, {"k": "package_code", "l": "Package Code"}]},
    {"title": "High Sea Sale and loads", "fields": [
        {"k": "high_sea_sale", "l": "High Sea Sale Flag", "req": 1, "t": "select", "o": _o("Yes - Yes", "No - No")}, {"k": "high_sea_charge", "l": "Purchase on High Seas (Additional Charge)"}, {"k": "misc_load", "l": "Miscellaneous Load"},
        {"k": "amendment_type", "l": "General Amendment Type", "t": "select", "o": _o("AMENDMENT - AMENDMENT")}]},
]
EXPORT_FORM = [
    {"title": "Exporter", "fields": [
        {"k": "exporter_name", "l": "Exporter Name", "req": 1, "span": 2}, {"k": "iec", "l": "Importer Exporter Code (IEC)", "req": 1}, {"k": "branch_sr_no", "l": "Branch Sr. No.", "req": 1, "ph": "e.g. 0 - INMUN1"}]},
    {"title": "Exporter Address", "fields": [
        {"k": "addr1", "l": "Address 1", "req": 1}, {"k": "addr2", "l": "Address 2"}, {"k": "addr3", "l": "Address 3 / City"}, {"k": "state", "l": "State"}, {"k": "pin", "l": "PIN"}]},
    {"title": "Exporter details", "fields": [
        {"k": "gstn_type", "l": "GSTN Type", "req": 1, "t": "select", "o": _o("GSN - GSTIN of Normal Persons", "GSG - GSTIN of Govt. Entities", "GSD - GSTIN of Diplomats", "PAN - PAN No", "TAN - TAN No", "PSP - Passport No (9 chars)", "ADH - Aadhar No (12 chars)")},
        {"k": "gstn_id", "l": "GSTN ID", "req": 1}, {"k": "exporter_type", "l": "Type of Exporter", "t": "select", "o": _o("R - Merchant Exporter", "F - Manufacturing Exporter", "C - Merchant Exporter Courier", "D - Manufacturer Exporter Courier")},
        {"k": "exporter_class", "l": "Exporter Class", "req": 1, "t": "select", "o": _o("P - Private", "G - Government", "W - Warehouse Shipping Bill", "O - Others")},
        {"k": "ad_code", "l": "Authorized Dealer (Bank) Code", "req": 1}, {"k": "epz_code", "l": "EPZ Code"}]},
    {"title": "Shipment", "fields": [
        {"k": "port_origin", "l": "Port of Origin", "ph": "e.g. INMAA"}, {"k": "country_destination", "l": "Country of Destination", "ph": "e.g. BE - BELGIUM"}, {"k": "country_discharge", "l": "Country of Discharge", "ph": "e.g. BE - BELGIUM"},
        {"k": "port_discharge", "l": "Port of Discharge", "ph": "e.g. BEANR"}, {"k": "state_of_origin", "l": "State of Origin"}, {"k": "port_loading", "l": "Port of Loading"}, {"k": "marks", "l": "Marks & Numbers", "span": 2}]},
    {"title": "Consignee", "fields": [
        {"k": "consignee", "l": "Consignee", "span": 2}, {"k": "cons_addr1", "l": "Consignee Address 1", "req": 1}, {"k": "cons_addr2", "l": "Consignee Address 2"}, {"k": "cons_addr3", "l": "Consignee Address 3"}, {"k": "cons_addr4", "l": "Consignee Address 4"},
        {"k": "cons_country", "l": "Consignee Country"}, {"k": "cons_category", "l": "Consignee Category"}]},
    {"title": "RBI waiver and amendment", "fields": [
        {"k": "rbi_waiver_no", "l": "RBI Waiver Number"}, {"k": "rbi_waiver_date", "l": "RBI Waiver Date", "t": "date"}, {"k": "amendment_type", "l": "General Amendment Type", "t": "select", "o": _o("AMENDMENT - AMENDMENT")}]},
]
FILING_TABS = {"Import": ["General", "IGM", "Invoice", "Product"], "Export": ["General", "Invoice", "Item", "Annexure-C", "Annexure-C1 (EOU)", "AR4 Details", "ETP"]}


def filing_form(direction: str) -> list[dict]:
    return EXPORT_FORM if direction == "Export" else IMPORT_FORM


def filing_defaults(db: Session, job: Shipment, row: dict, pd: dict) -> dict:
    """What the job and the Organization master already know, offered in the form until the filer types over it."""
    info = job.cha_info or {}
    ex, im = pd.get("exporter") or {}, pd.get("importer") or {}
    mode = {"Sea": "S", "Air": "A"}.get(job.mode, "L")
    loc = lambda port: (re.split(r"[\(\s]", (port or "").strip(), maxsplit=1)[0].upper() if port else "")
    if job.direction == "Export":
        kind = (ex.get("unit_kind") or "").lower()
        return {k: v for k, v in {"exporter_name": row["exporter"], "iec": ex.get("iec"), "gstn_type": "GSN" if ex.get("gstin") else ("PAN" if ex.get("pan") else None), "gstn_id": ex.get("gstin") or ex.get("pan"),
                                  "exporter_type": "F" if "manufactur" in kind else ("R" if "merchant" in kind else None), "exporter_class": "P", "ad_code": ex.get("ad_code"), "port_origin": loc(job.pol), "port_loading": loc(job.pol),
                                  "port_discharge": loc(job.pod), "consignee": row["importer"], "marks": job.commodity}.items() if v}
    sez = "sez" in (im.get("unit_kind") or "").lower()
    return {k: v for k, v in {"importer_name": row["importer"], "iec": im.get("iec"), "ad_code": im.get("ad_code"), "port_origin": loc(job.pol), "port_shipment": loc(job.pol), "payment_method": "T", "be_type": "Z" if sez else "H",
                              "mode_of_transport": mode, "filing_status": "N", "commercial_tax_type": "G" if im.get("gstin") else None, "commercial_tax_reg_no": im.get("gstin"), "customs_location": info.get("customs_location"), "high_sea_sale": "No"}.items() if v}


def validate_filing(job: Shipment, defaults: dict) -> dict:
    saved = (job.cha_info or {}).get("filing") or {}
    fields = [f for sec in filing_form(job.direction) for f in sec["fields"]]
    errors, checked = [], 0
    for f in fields:
        if not f.get("req"):
            continue
        checked += 1
        v = str(saved.get(f["k"]) if saved.get(f["k"]) not in (None, "") else defaults.get(f["k"], "")).strip()
        if not v:
            errors.append({"key": f["k"], "label": f["l"], "message": f["l"] + " is required"})
    return {"checked": checked, "passed": checked - len(errors), "errors": errors, "ready": not errors}


@router.get("/api/cha/jobs/{job_number:path}/validate")
def validate_job(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Checks the filing form: how many required fields were checked, how many passed and what is missing; `ready` when nothing is missing."""
    org = _tenant(user)
    job = _job(db, org, job_number)
    if job.job_type != "Customs":
        raise HTTPException(422, "Not a CHA job")
    row = cha_row(db, job, freight_of(db, job))
    d = filing_defaults(db, job, row, {"exporter": party_details(db, org, row["exporter"]), "importer": party_details(db, org, row["importer"])})
    return validate_filing(job, d)


def source_info(db: Session, job: Shipment, freight: Shipment | None, booking: Booking | None) -> dict:
    """Source information of a CHA job: the booking it belongs to (its number is the main reference), what was booked, and the order of work."""
    d = (booking.data if booking else None) or {}
    return {"booking_number": booking.number if booking else None, "booking_id": booking.id if booking else None, "booking_source": "System generated (direct job)" if d.get("auto_generated") else ("Booking" if booking else None),
            "services": _services(booking) if booking else [], "freight_job_number": freight.job_number if freight else None,
            "order": ("CHA first, then freight" if job.direction == "Export" else "Freight first, then CHA")}


@router.get("/api/cha/jobs/{job_number:path}")
def get_cha_job(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """One CHA job: details, stages, customs bills, documents, its freight job, the sibling CHA jobs of that freight job and its history."""
    from .models import JobEvent

    org = _tenant(user)
    job = _job(db, org, job_number)
    if job.job_type != "Customs":
        raise HTTPException(422, "Not a CHA job")
    freight = freight_of(db, job)
    booking = db.get(Booking, job.booking_id) if job.booking_id else None
    row = cha_row(db, job, freight, booking)
    siblings = [cha_row(db, j, freight, booking) for j in (cha_jobs_of(db, freight) if freight else []) if j.id != job.id]
    events = list(db.scalars(select(JobEvent).where(JobEvent.shipment_id == job.id).order_by(JobEvent.id.desc()).limit(200)))
    info = job.cha_info or {}
    return {**row, "info": {"file_ref": info.get("file_ref"), "customs_location": info.get("customs_location"), "cha_license": info.get("cha_license"), "mbl": info.get("mbl"), "hbl": info.get("hbl") or job.bl_awb,
                            "containers": info.get("containers"), "source": ("Freight job " + freight.job_number) if freight else "CHA screen"}, "source_info": source_info(db, job, freight, booking), "filing": (info.get("filing") or {}), "data": (info.get("data") or {}), "spec": cha_spec.IMPORT_SPEC if job.direction == "Import" else [], "filing_form": filing_form(job.direction), "filing_tabs": FILING_TABS[job.direction],
            "filing_defaults": filing_defaults(db, job, row, {"exporter": party_details(db, org, row["exporter"]), "importer": party_details(db, org, row["importer"])}), "filing_label": "Shipping Bill Filing" if job.direction == "Export" else "Bill of Entry Filing", "party_details": {"exporter": party_details(db, org, row["exporter"]), "importer": party_details(db, org, row["importer"])}, "stages": CHA_STATUSES, "delayed": is_delayed(job), "siblings": siblings,
            "events": [{"at": e.created_at, "type": e.event_type, "text": e.description, "previous": e.previous_value, "new": e.new_value, "by": e.actor_name, "reason": e.reason, "detail": e.detail} for e in events]}


class InfoIn(BaseModel):
    file_ref: str | None = Field(default=None, max_length=60)
    customs_location: str | None = Field(default=None, max_length=60)
    cha_license: str | None = Field(default=None, max_length=40)
    mbl: str | None = Field(default=None, max_length=60)
    hbl: str | None = Field(default=None, max_length=60)
    containers: str | None = Field(default=None, max_length=300)
    etd: date | None = None
    eta: date | None = None
    filing: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


@router.patch("/api/cha/jobs/{job_number:path}")
def update_cha_job(job_number: str, body: InfoIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    org = _tenant(user)
    job = _job(db, org, job_number)
    if job.job_type != "Customs":
        raise HTTPException(422, "Not a CHA job")
    jl.assert_job_editable(job, "the customs details")
    ch = body.model_dump(exclude_unset=True)
    if "filing" in ch:
        form = ch.pop("filing") or {}
        allowed = {f["k"] for sec in filing_form(job.direction) for f in sec["fields"]}
        bad = [k for k in form if k not in allowed]
        if bad:
            raise HTTPException(422, "Unknown filing field: " + ", ".join(sorted(bad)[:3]))
        clean = {k: str(v).strip()[:300] for k, v in form.items() if v is not None and str(v).strip()}
        old = (job.cha_info or {}).get("filing") or {}
        changes = [{"field": k, "from": old.get(k, ""), "to": clean.get(k, "")} for k in sorted(set(old) | set(clean)) if old.get(k, "") != clean.get(k, "")]
        job.cha_info = {**(job.cha_info or {}), "filing": clean}
        if changes:
            jl.log_event(db, job, user, "CHA_FILING_CHANGED", "OPERATIONS", "Filing details changed: " + ", ".join(c["field"] for c in changes[:6]) + (" ..." if len(changes) > 6 else ""), detail={"changes": changes[:60]})
    if "data" in ch:
        rules = cha_spec.collections()
        incoming = ch.pop("data") or {}
        cur = dict((job.cha_info or {}).get("data") or {})
        for name, rows in incoming.items():
            new = cha_spec.clean_collection(name, rows, rules)
            if new != cur.get(name, []):
                before = cur.get(name, [])
                jl.log_event(db, job, user, "CHA_DATA_CHANGED", "OPERATIONS", f"{rules[name]['title']}: {len(before)} → {len(new)} record(s)" + (" (edited)" if len(before) == len(new) else ""),
                             detail={"section": name, "before": len(before), "after": len(new), "changed": [i + 1 for i in range(min(len(before), len(new))) if before[i] != new[i]][:40]})
            cur[name] = new
        job.cha_info = {**(job.cha_info or {}), "data": cur}
    for k in ("etd", "eta"):
        if k in ch:
            setattr(job, k, ch.pop(k))
    oldinfo = job.cha_info or {}
    plain = [{"field": k, "from": str(oldinfo.get(k) or ""), "to": str(v or "")} for k, v in ch.items() if str(oldinfo.get(k) or "") != str(v or "")]
    job.cha_info = {**(job.cha_info or {}), **ch}
    if ch.get("hbl") or ch.get("mbl"):
        job.bl_awb = ch.get("hbl") or ch.get("mbl")
    fields = sorted(body.model_dump(exclude_unset=True))
    if plain:
        jl.log_event(db, job, user, "CHA_DETAILS_UPDATED", "OPERATIONS", "Customs details updated: " + ", ".join(c["field"] for c in plain), detail={"changes": plain})
    record_audit(db, "CHA_DETAILS_UPDATED", "OPERATIONS", user, org, "Shipment", str(job.id), {"job_number": job.job_number, "fields": fields})
    db.commit()
    return get_cha_job(job.job_number, user, db)
