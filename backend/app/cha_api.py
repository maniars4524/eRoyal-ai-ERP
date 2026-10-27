"""CHA (customs house agent) jobs. One booking has one freight job and one or MANY CHA jobs; every CHA job points at its freight job (`parent_job_id`), has its own number, its own Shipping Bill / Bill of Entry
records and its own customs status. The freight job shows all of them in its CUSTOMS / CHA JOB INFORMATION section; nothing is merged and a booking number is never made unique."""
from __future__ import annotations

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
from .models import Booking, CustomsBill, Customer, JobDetail, Shipment, User

router = APIRouter(tags=["cha jobs"])

CHA_STATUSES = ["CREATED", "DOCUMENTATION", "DATA ENTRY", "VALIDATED", "CHECKLIST", "CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION", "OOC / LEO CLEARED", "DELIVERED", "CLOSED"]
FILING_PENDING = {"CREATED", "DOCUMENTATION", "DATA ENTRY", "VALIDATED", "CHECKLIST"}          # not yet filed with customs
DONE = {"DELIVERED", "CLOSED"}
# dashboard cards: Draft = being prepared, Pending = ready to file, Filed = with customs, Cleared = out of charge / let export order
GROUPS = {"draft": {"CREATED", "DOCUMENTATION", "DATA ENTRY"}, "pending": {"VALIDATED", "CHECKLIST"}, "filed": {"CUSTOMS FILED", "CUSTOMS RESPONSE"}, "examination": {"EXAMINATION"}, "cleared": {"OOC / LEO CLEARED"},
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
    return shipper, cons


def documents_for(db: Session, freight: Shipment | None, cha: Shipment) -> list[dict]:
    """The customs documents of this kind of job: Export Air = AWB + Air Manifest Declaration, Export Sea = BL, Import = Cargo Arrival Notice + Delivery Order. They belong to the freight job's records."""
    src = freight or cha
    row = db.scalar(select(JobDetail).where(JobDetail.shipment_id == src.id))
    bl = (row.bl if row else None) or {}
    saved = (row.saved_sections if row else None) or {}
    ref = bl.get("hblNo") or bl.get("directNo") or bl.get("mblNo") or ""
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
    export = cha.direction == "Export"
    first = live[0] if live else None
    return {"id": cha.id, "cha_job_number": cha.job_number, "booking_number": booking.number if booking else None, "booking_id": cha.booking_id, "freight_job_number": freight.job_number if freight else None,
            "exporter": exporter, "importer": importer, "pol": cha.pol, "pod": cha.pod, "shipment_type": cha.direction, "shipment_mode": cha.mode,
            "shipping_bill_no": first.bill_no if (first and export) else None, "shipping_bill_date": first.bill_date if (first and export) else None,
            "bill_of_entry_no": first.bill_no if (first and not export) else None, "bill_of_entry_date": first.bill_date if (first and not export) else None,
            "bills": [{"id": b.id, "bill_type": b.bill_type, "bill_no": b.bill_no, "bill_date": b.bill_date, "status": b.status, "ooc_date": b.ooc_date, "examination": b.examination} for b in bills],
            "customs_status": cha.customs_status or "CREATED", "job_stage": cha.job_stage, "etd": cha.etd, "eta": cha.eta, "created_at": cha.created_at,
            "documents": documents_for(db, freight, cha), "linked_by": "parent" if cha.parent_job_id else "booking"}


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
    return {"freight_job_number": freight.job_number, "freight_job_id": freight.id, "booking_number": booking.number if booking else None, "count": len(jobs),
            "freight": {"exporter": ex, "importer": im, "pol": freight.pol, "pod": freight.pod, "shipment_type": freight.direction, "shipment_mode": freight.mode}, "cha_jobs": [cha_row(db, j, freight, booking) for j in jobs]}


def _job(db: Session, org: int, job_number: str) -> Shipment:
    j = db.scalar(select(Shipment).where(Shipment.organization_id == org, Shipment.job_number == job_number))
    if not j:
        raise HTTPException(404, "Job not found")
    return j


@router.get("/api/jobs/{job_number}/cha-jobs")
def list_cha_jobs(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    return consolidated(db, _job(db, _tenant(user), job_number))


class ChaIn(BaseModel):
    exporter: str | None = Field(default=None, max_length=200)
    importer: str | None = Field(default=None, max_length=200)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    exception_reason: str = Field(default="", max_length=300)


@router.post("/api/jobs/{job_number}/cha-jobs", status_code=201)
def add_cha_job(job_number: str, body: ChaIn, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    """Another CHA job under the same booking and freight job. The exporter, importer, port of loading and port of discharge must match the freight job; an authorised user can record an exception with a reason.
    No new freight job is created and the booking number is shared."""
    from .shipments_api import _next_job_number

    org = _tenant(user)
    freight = _job(db, org, job_number)
    if freight.job_type == "Customs":
        freight = freight_of(db, freight)
        if freight is None:
            raise HTTPException(409, "This CHA job has no freight job to add another CHA job to")
    if freight.direction not in ("Export", "Import"):
        raise HTTPException(422, "CHA jobs apply to export and import shipments")
    jl.assert_job_editable(freight, "its CHA jobs")
    fex, fim = parties_of(db, freight)
    ex, im = (body.exporter or fex).strip(), (body.importer or fim).strip()
    pol, pod = (body.pol or freight.pol or "").strip(), (body.pod or freight.pod or "").strip()
    wrong = []
    if _norm(ex) != _norm(fex):
        wrong.append(f"Exporter ({ex or 'blank'}) differs from the freight job ({fex or 'blank'})")
    if _norm(im) != _norm(fim):
        wrong.append(f"Importer ({im or 'blank'}) differs from the freight job ({fim or 'blank'})")
    if location_code(pol) != location_code(freight.pol):
        wrong.append(f"Port of loading ({pol or 'blank'}) differs from the freight job ({freight.pol or 'blank'})")
    if location_code(pod) != location_code(freight.pod):
        wrong.append(f"Port of discharge ({pod or 'blank'}) differs from the freight job ({freight.pod or 'blank'})")
    can_override = has_permission(user, "cha.exception")
    if wrong:
        if not (can_override and len(body.exception_reason.strip()) >= 5):
            raise HTTPException(409, {"code": "CHA_MISMATCH", "message": "The CHA job does not match the freight job: " + "; ".join(wrong), "mismatches": wrong, "can_override": can_override})
    cha = Shipment(organization_id=org, job_number=_next_job_number(db, org, freight.mode, freight.direction, freight.container_mode, user), job_type="Customs", booking_id=freight.booking_id, parent_job_id=freight.id, mode=freight.mode,
                   direction=freight.direction, container_mode=freight.container_mode, pol=pol or None, pod=pod or None, etd=freight.etd, eta=freight.eta, customer_id=freight.customer_id, consignee=im or None, packages=freight.packages,
                   gross_weight=freight.gross_weight, volume=freight.volume, package_type=freight.package_type, commodity=freight.commodity, status="Booked", created_by=user.id, branch=freight.branch, salesperson=freight.salesperson,
                   customs_status="CREATED")
    db.add(cha)
    db.flush()
    db.add(JobDetail(organization_id=org, shipment_id=cha.id, parties={"shipper": ex, "consignee": im}, cargo={}, containers=[], dims={}, bl={}, saved_sections={"parties": datetime.now(timezone.utc).isoformat()}, version=1, updated_by=user.id))
    jl.on_job_created(db, cha, user, f"additional CHA job of {freight.job_number}", {"freight_job": freight.job_number})
    record_audit(db, "CHA_JOB_ADDED", "OPERATIONS", user, org, "Shipment", str(cha.id), {"job_number": cha.job_number, "freight_job": freight.job_number, "booking_id": freight.booking_id, "exception": bool(wrong), "reason": body.exception_reason.strip() if wrong else ""})
    db.commit()
    out = consolidated(db, freight)
    out["created"] = cha.job_number
    return out


class StatusIn(BaseModel):
    status: str
    remarks: str = Field(default="", max_length=300)


@router.patch("/api/cha/jobs/{job_number}/status")
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
    jobs = list(db.scalars(select(Shipment).where(Shipment.organization_id == org, Shipment.job_type == "Customs")))
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
    return {"groups": groups, "total": len(jobs), "pending": len(pending), "delayed": len(delayed), "completed": len(jobs) - len(pending), "import_jobs": sum(1 for j in jobs if j.direction == "Import"), "export_jobs": sum(1 for j in jobs if j.direction == "Export"),
            "pending_documents": sum(1 for j in pending if j.id not in with_bill and (j.customs_status or "CREATED") not in FILING_PENDING), "pending_customs_filing": sum(1 for j in jobs if (j.customs_status or "CREATED") in FILING_PENDING),
            "examination": by_status["EXAMINATION"], "ooc_pending_import": sum(1 for j in jobs if j.direction == "Import" and (j.customs_status or "CREATED") in ("CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION")),
            "ep_copy_pending_export": sum(1 for j in jobs if j.direction == "Export" and (j.customs_status or "CREATED") in ("CUSTOMS FILED", "CUSTOMS RESPONSE", "EXAMINATION", "OOC / LEO CLEARED")),
            "delivery_pending": sum(1 for j in pending if (j.customs_status or "CREATED") == "OOC / LEO CLEARED"), "closure_pending": sum(1 for j in jobs if (j.customs_status or "CREATED") == "DELIVERED"),
            "matrix": matrix, "by_status": by_status, "statuses": CHA_STATUSES}


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
    exception_reason: str = Field(default="", max_length=300)


@router.post("/api/cha/jobs", status_code=201)
def create_cha_job(body: NewChaIn, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    """Create a CHA job from the CHA screen (Export or Import, Air or Sea). Linked to a freight job, under the same rules as an additional CHA job, when a freight job number is given; otherwise a stand-alone customs job."""
    from .shipments_api import _next_job_number

    org = _tenant(user)
    if body.freight_job_number.strip():
        freight = _job(db, org, body.freight_job_number.strip())
        if freight.direction != body.job_type or freight.mode != body.mode:
            raise HTTPException(409, f"The freight job is {freight.direction} {freight.mode}; a {body.job_type} {body.mode} CHA job cannot be linked to it")
        out = add_cha_job(freight.job_number, ChaIn(exporter=body.exporter or None, importer=body.importer or None, pol=body.pol or None, pod=body.pod or None, exception_reason=body.exception_reason), user, db)
        cha = _job(db, org, out["created"])
    else:
        if body.customer_id and not db.scalar(select(Customer.id).where(Customer.id == body.customer_id, Customer.organization_id == org)):
            raise HTTPException(404, "Organization not found")
        cha = Shipment(organization_id=org, job_number=_next_job_number(db, org, body.mode, body.job_type, None, user), job_type="Customs", mode=body.mode, direction=body.job_type, pol=body.pol or None, pod=body.pod or None,
                       etd=body.etd, eta=body.eta, customer_id=body.customer_id, consignee=body.importer or None, status="Booked", created_by=user.id, customs_status="CREATED")
        db.add(cha)
        db.flush()
        jl.on_job_created(db, cha, user, "CHA screen", {"standalone": True})
        record_audit(db, "CHA_JOB_CREATED", "OPERATIONS", user, org, "Shipment", str(cha.id), {"job_number": cha.job_number, "type": body.job_type, "mode": body.mode})
    cha.etd = body.etd or cha.etd
    cha.eta = body.eta or cha.eta
    given = {"exporter": body.exporter, "importer": body.importer, "mbl": body.mbl, "hbl": body.hbl, "containers": body.containers, "file_ref": body.file_ref, "customs_location": body.customs_location, "cha_license": body.cha_license}
    cha.cha_info = {**(cha.cha_info or {}), **{k: v for k, v in given.items() if v}}
    if body.hbl or body.mbl:
        cha.bl_awb = body.hbl or body.mbl
    db.commit()
    return get_cha_job(cha.job_number, user, db)


@router.get("/api/cha/jobs/{job_number}")
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
    events = list(db.scalars(select(JobEvent).where(JobEvent.shipment_id == job.id).order_by(JobEvent.id.desc()).limit(40)))
    info = job.cha_info or {}
    return {**row, "info": {"file_ref": info.get("file_ref"), "customs_location": info.get("customs_location"), "cha_license": info.get("cha_license"), "mbl": info.get("mbl"), "hbl": info.get("hbl") or job.bl_awb,
                            "containers": info.get("containers"), "source": ("Freight job " + freight.job_number) if freight else "CHA screen"}, "stages": CHA_STATUSES, "delayed": is_delayed(job), "siblings": siblings,
            "events": [{"at": e.created_at, "type": e.event_type, "text": e.description, "previous": e.previous_value, "new": e.new_value} for e in events]}


class InfoIn(BaseModel):
    file_ref: str | None = Field(default=None, max_length=60)
    customs_location: str | None = Field(default=None, max_length=60)
    cha_license: str | None = Field(default=None, max_length=40)
    mbl: str | None = Field(default=None, max_length=60)
    hbl: str | None = Field(default=None, max_length=60)
    containers: str | None = Field(default=None, max_length=300)
    etd: date | None = None
    eta: date | None = None


@router.patch("/api/cha/jobs/{job_number}")
def update_cha_job(job_number: str, body: InfoIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    org = _tenant(user)
    job = _job(db, org, job_number)
    if job.job_type != "Customs":
        raise HTTPException(422, "Not a CHA job")
    jl.assert_job_editable(job, "the customs details")
    ch = body.model_dump(exclude_unset=True)
    for k in ("etd", "eta"):
        if k in ch:
            setattr(job, k, ch.pop(k))
    job.cha_info = {**(job.cha_info or {}), **ch}
    if ch.get("hbl") or ch.get("mbl"):
        job.bl_awb = ch.get("hbl") or ch.get("mbl")
    fields = sorted(body.model_dump(exclude_unset=True))
    jl.log_event(db, job, user, "CHA_DETAILS_UPDATED", "OPERATIONS", "Customs details updated", detail={"fields": fields})
    record_audit(db, "CHA_DETAILS_UPDATED", "OPERATIONS", user, org, "Shipment", str(job.id), {"job_number": job.job_number, "fields": fields})
    db.commit()
    return get_cha_job(job.job_number, user, db)
