"""Job lifecycle API: stage, milestones, manual stage change, closure checklist, close, reopen, stage/closure history, activity log + export.

Nothing here edits or deletes history. Permissions: job.view_history, job.export_history, job.change_stage, job.close, job.reopen,
job.exceptional_close (plus shipment.read / shipment.update for milestones). Authorisation, eligibility and totals are decided here, never in the browser.
"""
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import job_lifecycle as jl
from .auth import get_current_user, has_permission, permission_required
from .auth_api import record_audit
from .db import SessionLocal, get_db
from .models import JobClosure, JobEvent, JobMilestone, JobStageHistory, Shipment, User

router = APIRouter(prefix="/api/jobs", tags=["job-lifecycle"])
REOPEN_REASONS = ["Additional Vendor Invoice Received", "Additional Customer Charges", "Documentation Correction", "Shipment Amendment", "Cost Adjustment", "Invoice Revision", "Operational Correction", "Other"]


def _job(db: Session, user: User, job_number: str, lock: bool = False) -> Shipment:
    if user.organization_id is None:
        raise HTTPException(403, "Jobs are tenant-scoped")
    q = select(Shipment).where(Shipment.job_number == job_number, Shipment.organization_id == user.organization_id)
    job = db.scalar(q.with_for_update() if lock else q)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _ts(v):
    return v.isoformat() if v else None


def _can(user: User, code: str) -> bool:
    return has_permission(user, code)


def _denied(db0: Session, job: Shipment, user: User, action: str, code: str) -> None:
    """Permission-denied attempts on protected job actions are part of the audit trail (written in their own transaction)."""
    job_id, user_id = job.id, user.id
    db0.rollback()  # release the row lock held by the request before the audit row is written
    db = SessionLocal()
    try:
        j = db.get(Shipment, job_id)
        u = db.get(User, user_id)
        jl.log_event(db, j, u, "PERMISSION_DENIED", "SECURITY", f"{action} refused: missing permission {code}", result="REJECTED", detail={"permission": code, "action": action})
        db.commit()
    finally:
        db.close()
    raise HTTPException(403, f"You do not have permission to {action}")


def _fail_event(db0: Session, job_id: int, user_id: int, event_type: str, category: str, description: str, detail: dict, reason: str | None = None) -> None:
    db0.rollback()  # release the row lock held by the request before the audit row is written
    db = SessionLocal()
    try:
        j = db.get(Shipment, job_id)
        u = db.get(User, user_id)
        jl.log_event(db, j, u, event_type, category, description, result="FAILED", detail=detail, reason=reason)
        db.commit()
    finally:
        db.close()


def _closure_out(c: JobClosure) -> dict:
    return {"id": c.id, "cycle_no": c.cycle_no, "action": c.action, "previous_status": c.previous_status, "new_status": c.new_status, "reason": c.reason, "remarks": c.remarks, "actor": c.actor_name, "at": _ts(c.created_at),
            "approval_ref": c.approval_ref, "checklist": c.checklist, "final_buy": None if c.final_buy is None else str(c.final_buy), "final_sell": None if c.final_sell is None else str(c.final_sell),
            "final_margin": None if c.final_margin is None else str(c.final_margin), "previous_closure_id": c.previous_closure_id, "event_id": c.event_id}


def _event_out(e: JobEvent, full: bool = True) -> dict:
    return {"id": e.id, "event_type": e.event_type, "category": e.category, "description": e.description, "previous_value": e.previous_value, "new_value": e.new_value, "actor_type": e.actor_type, "actor_id": e.actor_id,
            "actor": e.actor_name, "source": e.source, "reason": e.reason, "related_type": e.related_type, "related_id": e.related_id, "correlation_id": e.correlation_id, "approval_ref": e.approval_ref,
            "result": e.result, "at": _ts(e.created_at), "job_number": e.job_number, "detail": e.detail or {}}


def _stage_actor(db: Session, job: Shipment) -> str | None:
    row = db.scalar(select(JobStageHistory).where(JobStageHistory.shipment_id == job.id).order_by(JobStageHistory.id.desc()).limit(1))
    return (row.actor_name if row and row.actor_name else None) or ("System (automatic)" if row else None)


def lifecycle_out(db: Session, job: Shipment, user: User) -> dict:
    ms = jl.milestone_map(db, job)
    req = jl.required_milestones(job)
    last_closer = db.get(User, job.last_closed_by) if job.last_closed_by else None
    count = db.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.organization_id == job.organization_id, JobEvent.shipment_id == job.id)) or 0
    closed = job.closure_status == "CLOSED"
    return {"job_number": job.job_number, "job_stage": job.job_stage, "stage_source": job.stage_source, "stage_reason": job.stage_reason, "stage_updated_at": _ts(job.stage_updated_at), "stage_updated_by": _stage_actor(db, job), "closure_status": job.closure_status,
            "closure_cycle": job.closure_cycle, "last_closed_at": _ts(job.last_closed_at), "last_closed_by": jl.display_name(last_closer) if last_closer else None, "reopen_count": job.reopen_count,
            "last_activity_at": _ts(job.last_activity_at), "event_count": count, "stages": jl.STAGES, "manual_stages": jl.MANUAL_STAGES, "reopen_reasons": REOPEN_REASONS,
            "milestones": [{"code": c, "label": jl.MILESTONE_LABEL.get(c, c), "required": c in req, "stage": jl.MILESTONES.get(c), "done": c in ms, "actual_at": _ts(ms[c].actual_at) if c in ms else None,
                            "remarks": ms[c].remarks if c in ms else None, "source": "booking" if c in ms and ms[c].id is None else "user"} for c in jl.applicable_milestones(job)],
            "actions": {"can_close": _can(user, "job.close") and not closed, "can_reopen": _can(user, "job.reopen") and closed, "can_change_stage": _can(user, "job.change_stage") and job.closure_status == "OPEN", "can_view_history": _can(user, "job.view_history"),
                        "can_export_history": _can(user, "job.export_history"), "can_record_milestone": _can(user, "shipment.update") and not closed, "can_exceptional_close": _can(user, "job.exceptional_close") and jl.setting_enabled(db, job.organization_id, "JOB_EXCEPTIONAL_CLOSURE")}}


@router.get("/stages")
def stage_config(user: User = Depends(permission_required("shipment.read"))):
    return {"stages": jl.STAGES, "milestones": [{"code": c, "label": jl.MILESTONE_LABEL.get(c, c), "stage": jl.MILESTONES.get(c)} for c in jl.ALL_MILESTONES], "reopen_reasons": REOPEN_REASONS}


@router.get("/{job_number}/source")
def job_source(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Where the job came from: the quotation, the booking, or created directly."""
    from .models import Booking, Quotation

    job = _job(db, user, job_number)
    booking = db.get(Booking, job.booking_id) if job.booking_id else None
    qid = booking.quotation_id if booking and booking.quotation_id else None
    quote = db.get(Quotation, qid) if qid else db.scalar(select(Quotation).where(Quotation.organization_id == job.organization_id, Quotation.job_number == job.job_number))
    creator = db.get(User, job.created_by) if job.created_by else None
    first = db.scalar(select(JobEvent).where(JobEvent.shipment_id == job.id, JobEvent.event_type == "JOB_CREATED").order_by(JobEvent.id).limit(1))
    via = "Booking" if booking else ("Quotation" if quote else "Created directly")
    names = lambda uid: jl.display_name(db.get(User, uid)) if uid else None
    return {"job_number": job.job_number, "created_via": via, "created_at": _ts(job.created_at), "created_by": jl.display_name(creator) if creator else None, "creation_note": (first.description if first else None),
            "booking": {"number": booking.number, "date": str(booking.booking_date), "status": booking.status, "carrier": booking.carrier, "confirmed_at": _ts(booking.confirmed_at), "created_by": names(booking.created_by), "services": (booking.data or {}).get("services")} if booking else None,
            "quotation": {"number": quote.number, "date": str(quote.quote_date), "status": quote.status, "party": quote.party_name, "prepared_by": names(quote.prepared_by), "approved_by": names(quote.approved_by), "approved_at": _ts(quote.approved_at), "valid_until": str(quote.valid_until) if quote.valid_until else None,
                          "reference": (quote.header or {}).get("reference_no")} if quote else None}


def company_block(db: Session, job: Shipment) -> dict:
    from .job_details_api import _profile, company_code
    from .models import Organization

    org = db.get(Organization, job.organization_id)
    return {"name": org.legal_name or org.name, "logo": org.logo, "code": company_code(org), "profile": _profile(db, org.id)}


def booking_block(db: Session, job: Shipment, b) -> dict:
    d = (b.data if b else {}) or {}
    return {"container_mode": job.container_mode, "number": b.number if b else None, "carrier": b.carrier if b else None, "vessel": b.vessel if b else None, "carrier_booking_no": b.carrier_booking_no if b else None, "etd": b.etd if b else job.etd, "eta": b.eta if b else job.eta,
            "marks": d.get("marks"), "place_of_receipt": d.get("place_of_receipt"), "place_of_delivery": d.get("place_of_delivery"), "freight_pp": d.get("freight_pp"), "service_type": d.get("service_type")}


def party_block(db: Session, job: Shipment, b) -> dict:
    from .job_details_api import _out, _row

    o = _out(db, job, _row(db, job))["parties"]
    d = (b.data if b else {}) or {}
    names = {"shipper": o.get("shipper"), "consignee": o.get("consignee"), "notify1": o.get("notify1"), "notify2": o.get("notify2"), "deliveryAgent": o.get("deliveryAgent"), "originAgent": o.get("originAgent")}
    book_addr = {"shipper": d.get("shipper_address"), "consignee": d.get("consignee_address"), "notify1": d.get("notify_address")}
    return {k: party_text(db, job.organization_id, v, book_addr.get(k)) for k, v in names.items()}


def job_detail_snapshot(db: Session, job: Shipment) -> dict:
    from .job_details_api import _out, _row

    o = _out(db, job, _row(db, job))   # saved details, else what the booking already knows
    return {k: o[k] for k in ("parties", "cargo", "containers", "dims", "bl")}


def party_text(db: Session, org_id: int, name: str | None, fallback: str | None = None) -> str:
    """Name and address lines of a party, from the Organization Master (first active branch with an address), else what the booking carried."""
    from .models import Customer, OrgBranch

    if not name or name.startswith("__"):
        return ""
    c = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == name))
    b = db.scalar(select(OrgBranch).where(OrgBranch.customer_id == c.id, OrgBranch.address.is_not(None), OrgBranch.address != "").order_by(OrgBranch.sl_no, OrgBranch.id)) if c else None
    lines = [name]
    if b:
        city = " ".join(x for x in (b.city, b.pincode) if x)
        lines += [x for x in (b.address, b.address2, city, b.state, b.country) if x]
        if b.phone:
            lines.append("TEL: " + b.phone)
    elif fallback:
        lines.append(fallback)
    return "\n".join(lines)


@router.get("/{job_number}/report-data")
def report_data(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Everything the job documents need, taken from the booking, the job, the customs clearance bills and the posted invoices: BL / AWB, invoices, SB, BE."""
    from . import customs_api
    from .models import Booking, Document

    job = _job(db, user, job_number)
    booking = db.get(Booking, job.booking_id) if job.booking_id else None
    cus = customs_api.summary(db, job)
    docs = list(db.scalars(select(Document).where(Document.organization_id == job.organization_id, Document.job_number == job.job_number, Document.status != "CANCELLED").order_by(Document.id)))
    bills = [b for b in cus["bills"] if b["status"] != "Cancelled"]
    return {"job_number": job.job_number, "job_type": job.job_type, "mode": job.mode, "direction": job.direction, "booking_number": booking.number if booking else None,
            "bl_awb": {"label": "AWB" if job.mode == "Air" else "BL", "number": job.bl_awb}, "pol": job.pol, "pod": job.pod, "packages": job.packages, "gross_weight": str(job.gross_weight) if job.gross_weight is not None else None,
            "volume": str(job.volume) if job.volume is not None else None, "commodity": job.commodity,
            "details": job_detail_snapshot(db, job), "company": company_block(db, job), "booking": booking_block(db, job, booking), "party_text": party_block(db, job, booking),
            "invoices": [{"doc_number": d.doc_number, "doc_type": d.doc_type, "doc_date": d.doc_date, "status": d.status, "total": str(d.grand_total)} for d in docs],
            "shipping_bills": [b for b in bills if b["bill_type"] == "SB"], "bills_of_entry": [b for b in bills if b["bill_type"] == "BE"], "customs_bill_count": cus["count"], "customs_jobs": cus["customs_jobs"]}


@router.get("/{job_number}/lifecycle")
def get_lifecycle(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    return lifecycle_out(db, _job(db, user, job_number), user)


# ------------------------------------------------------------------ milestones
class MilestoneIn(BaseModel):
    actual_at: datetime
    remarks: str | None = Field(default=None, max_length=1000)
    correction_reason: str | None = Field(default=None, max_length=1000)


@router.put("/{job_number}/milestones/{code}")
def record_milestone(job_number: str, code: str, body: MilestoneIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "milestones")
    if code not in jl.applicable_milestones(job):
        raise HTTPException(422, f"'{code}' does not apply to a {job.mode} {job.direction} job")
    at = body.actual_at if body.actual_at.tzinfo else body.actual_at.replace(tzinfo=timezone.utc)
    if at > jl.now() + timedelta(minutes=5):
        raise HTTPException(422, "An actual milestone cannot be in the future. Planned dates do not complete milestones")
    row = db.scalar(select(JobMilestone).where(JobMilestone.shipment_id == job.id, JobMilestone.code == code).with_for_update())
    corr = uuid.uuid4().hex[:16]
    label = jl.MILESTONE_LABEL.get(code, code)
    if row is None:
        db.add(JobMilestone(organization_id=job.organization_id, shipment_id=job.id, code=code, actual_at=at, remarks=body.remarks, confirmed_by=user.id))
        jl.log_event(db, job, user, "MILESTONE_CONFIRMED", "OPERATIONS", f"{label} confirmed", new=at.isoformat(), related_type="MILESTONE", related_id=code, correlation_id=corr, detail={"remarks": body.remarks})
    else:
        if not (body.correction_reason or "").strip():
            raise HTTPException(409, "This milestone is already confirmed. To correct it, give a correction reason (the original value stays in the history)")
        previous = row.actual_at
        row.actual_at, row.remarks, row.confirmed_by = at, body.remarks or row.remarks, user.id
        jl.log_event(db, job, user, "MILESTONE_CORRECTED", "OPERATIONS", f"{label} corrected", previous=previous.isoformat(), new=at.isoformat(), reason=body.correction_reason, related_type="MILESTONE", related_id=code, correlation_id=corr)
    db.flush()
    jl.recompute_stage(db, job, "MILESTONE:" + code, user, corr)
    db.commit()
    return lifecycle_out(db, job, user)


# ------------------------------------------------------------------ manual stage change
class StageIn(BaseModel):
    stage: str
    reason: str = Field(min_length=3, max_length=1000)
    confirm_conflict: bool = False
    approval_ref: str | None = Field(default=None, max_length=60)


@router.post("/{job_number}/stage")
def change_stage(job_number: str, body: StageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = _job(db, user, job_number, lock=True)
    if not _can(user, "job.change_stage"):
        _denied(db, job, user, "change the job stage", "job.change_stage")
    jl.assert_job_editable(job, "the stage")
    if body.stage in ("Closed", "Reopened"):
        raise HTTPException(422, "Use Close Job or Reopen Job: those stages are set only by the closure actions")
    if body.stage not in jl.MANUAL_STAGES:
        raise HTTPException(422, "Unknown job stage")
    if job.closure_status == "REOPENED":
        raise HTTPException(409, "A reopened job stays Reopened until it is closed again")
    if body.stage == job.job_stage:
        raise HTTPException(409, "The job is already at this stage")
    jl.set_stage(db, job, body.stage, "MANUAL", user, trigger="MANUAL_CHANGE", reason=body.reason.strip(), approval_ref=body.approval_ref)
    db.commit()
    return lifecycle_out(db, job, user)


# ------------------------------------------------------------------ closure
class AckIn(BaseModel):
    no_buy_cost: bool = False
    accrual: bool = False
    cost_sheet_reviewed: bool = False
    discrepancy: bool = False


class CloseIn(BaseModel):
    ack: AckIn = Field(default_factory=AckIn)
    remarks: str | None = Field(default=None, max_length=2000)
    exceptional: bool = False
    justification: str | None = Field(default=None, max_length=2000)


class ReopenIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    remarks: str | None = Field(default=None, max_length=2000)


@router.post("/{job_number}/closure/validate")
def validate_closure(job_number: str, body: AckIn, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    out = jl.checklist(db, job, user, body.model_dump())
    out.update({"job_number": job.job_number, "job_stage": job.job_stage, "closure_status": job.closure_status, "exceptional_allowed": jl.setting_enabled(db, job.organization_id, "JOB_EXCEPTIONAL_CLOSURE") and _can(user, "job.exceptional_close")})
    return out


@router.post("/{job_number}/close")
def close_job(job_number: str, body: CloseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    job = _job(db, user, job_number, lock=True)
    if not _can(user, "job.close"):
        _denied(db, job, user, "close jobs", "job.close")
    if idempotency_key:
        prior = db.scalar(select(JobEvent).where(JobEvent.organization_id == job.organization_id, JobEvent.idempotency_key == idempotency_key))
        if prior is not None:
            return {"already_processed": True, **lifecycle_out(db, job, user)}
    if job.closure_status == "CLOSED":
        raise HTTPException(409, f"Job {job.job_number} is already closed")
    cl = jl.checklist(db, job, user, body.ack.model_dump())
    exceptional = False
    approval_ref = None
    if cl["blocking"]:
        if not body.exceptional:
            _fail_event(db, job.id, user.id, "JOB_CLOSURE_FAILED", "CLOSURE", f"Closure refused: {len(cl['blocking'])} requirement(s) pending", {"pending": [i["key"] for i in cl["blocking"]]})
            raise HTTPException(422, {"message": "Closure requirements are not met", "pending": cl["blocking"]})
        if not jl.setting_enabled(db, job.organization_id, "JOB_EXCEPTIONAL_CLOSURE"):
            _fail_event(db, job.id, user.id, "JOB_CLOSURE_FAILED", "CLOSURE", "Exceptional closure refused: not allowed by company policy", {"pending": [i["key"] for i in cl["blocking"]]})
            raise HTTPException(409, "Exceptional closure is not enabled by company policy")
        if not _can(user, "job.exceptional_close"):
            _denied(db, job, user, "approve an exceptional closure", "job.exceptional_close")
        if len((body.justification or "").strip()) < 10:
            raise HTTPException(422, "A justification of at least 10 characters is required for an exceptional closure")
        exceptional = True
        approval_ref = f"EXC-{user.id}-{jl.now():%Y%m%d%H%M%S}"
    previous = db.scalar(select(JobClosure).where(JobClosure.shipment_id == job.id, JobClosure.action.in_(("CLOSE", "EXCEPTIONAL"))).order_by(JobClosure.id.desc()))
    cycle = job.closure_cycle + 1
    t = jl.money_totals(db, job)
    corr = uuid.uuid4().hex[:16]
    snapshot = [{k: i.get(k) for k in ("key", "group", "label", "status", "detail")} for i in cl["items"]]
    prev_status = job.closure_status
    ev = jl.log_event(db, job, user, "JOB_CLOSED_EXCEPTIONAL" if exceptional else "JOB_CLOSED", "CLOSURE", f"Job closed{' (exceptional closure)' if exceptional else ''}", previous=prev_status, new="CLOSED", reason=body.justification if exceptional else None,
                      approval_ref=approval_ref, correlation_id=corr, idempotency_key=idempotency_key, detail={"stage": job.job_stage, "cycle": cycle, "final_buy": str(t["buy"]), "final_sell": str(t["sell"]), "final_margin": str(t["margin"]), "remarks": body.remarks})
    try:
        db.add(JobClosure(organization_id=job.organization_id, shipment_id=job.id, job_number=job.job_number, cycle_no=cycle, action="EXCEPTIONAL" if exceptional else "CLOSE", previous_status=prev_status, new_status="CLOSED",
                          reason=body.justification if exceptional else None, remarks=body.remarks, actor_id=user.id, actor_name=jl.display_name(user), approval_ref=approval_ref, checklist=snapshot,
                          final_buy=t["buy"], final_sell=t["sell"], final_margin=t["margin"], previous_closure_id=previous.id if previous else None, event_id=ev.id))
        job.closure_status, job.closure_cycle, job.last_closed_at, job.last_closed_by = "CLOSED", cycle, jl.now(), user.id
        jl.set_stage(db, job, "Closed", "AUTO", user, trigger="JOB_CLOSED", correlation_id=corr)
        record_audit(db, "JOB_CLOSED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "cycle": cycle, "exceptional": exceptional})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This job was closed by another request. Refresh and check its status")
    return lifecycle_out(db, job, user)


@router.post("/{job_number}/reopen")
def reopen_job(job_number: str, body: ReopenIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    job = _job(db, user, job_number, lock=True)
    if not _can(user, "job.reopen"):
        _denied(db, job, user, "reopen jobs", "job.reopen")
    if idempotency_key:
        prior = db.scalar(select(JobEvent).where(JobEvent.organization_id == job.organization_id, JobEvent.idempotency_key == idempotency_key))
        if prior is not None:
            return {"already_processed": True, **lifecycle_out(db, job, user)}
    if job.closure_status != "CLOSED":
        _fail_event(db, job.id, user.id, "JOB_REOPEN_FAILED", "REOPEN", f"Reopen refused: the job is {job.closure_status}", {"closure_status": job.closure_status}, body.reason)
        raise HTTPException(409, "Only a closed job can be reopened")
    last_close = db.scalar(select(JobClosure).where(JobClosure.shipment_id == job.id, JobClosure.action.in_(("CLOSE", "EXCEPTIONAL"))).order_by(JobClosure.id.desc()))
    corr = uuid.uuid4().hex[:16]
    ev = jl.log_event(db, job, user, "JOB_REOPENED", "REOPEN", "Job reopened", previous="CLOSED", new="REOPENED", reason=body.reason.strip(), correlation_id=corr, idempotency_key=idempotency_key,
                      related_type="JOB_CLOSURE", related_id=last_close.id if last_close else None, detail={"remarks": body.remarks, "previous_closure_event": last_close.event_id if last_close else None, "stage": job.job_stage})
    db.add(JobClosure(organization_id=job.organization_id, shipment_id=job.id, job_number=job.job_number, cycle_no=job.closure_cycle, action="REOPEN", previous_status="CLOSED", new_status="REOPENED", reason=body.reason.strip(),
                      remarks=body.remarks, actor_id=user.id, actor_name=jl.display_name(user), checklist=[], previous_closure_id=last_close.id if last_close else None, event_id=ev.id))
    job.closure_status, job.reopen_count = "REOPENED", job.reopen_count + 1
    record_audit(db, "JOB_REOPENED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "reason": body.reason})
    jl.set_stage(db, job, "Reopened", "AUTO", user, trigger="JOB_REOPENED", reason=body.reason.strip(), correlation_id=corr)
    db.commit()
    return lifecycle_out(db, job, user)


# ------------------------------------------------------------------ history
@router.get("/{job_number}/closures")
def closure_history(job_number: str, user: User = Depends(permission_required("job.view_history")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    return [_closure_out(c) for c in db.scalars(select(JobClosure).where(JobClosure.shipment_id == job.id).order_by(JobClosure.id.desc()))]


@router.get("/{job_number}/stage-history")
def stage_history(job_number: str, user: User = Depends(permission_required("job.view_history")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    return [{"id": h.id, "previous_stage": h.previous_stage, "new_stage": h.new_stage, "source": h.source, "trigger": h.trigger, "reason": h.reason, "actor": h.actor_name, "at": _ts(h.created_at), "related_ref": h.related_ref}
            for h in db.scalars(select(JobStageHistory).where(JobStageHistory.shipment_id == job.id).order_by(JobStageHistory.id.desc()))]


CATEGORY_FILTERS = {"header": ("JOB", "HEADER"), "operations": ("OPERATIONS",), "documents": ("DOCUMENTS",), "buycost": ("BUYCOST",), "sellcost": ("SELLCOST",), "finance": ("FINANCE",), "stage": ("STAGE",),
                    "closure": ("CLOSURE",), "reopen": ("REOPEN",), "approvals": ("APPROVAL",), "security": ("SECURITY",), "system": ("SYSTEM",)}


def _activity_query(job: Shipment, category, actor_id, event_type, q, date_from, date_to, result):
    cond = [JobEvent.organization_id == job.organization_id, JobEvent.shipment_id == job.id]
    if category:
        cats = CATEGORY_FILTERS.get(category.lower()) or (category.upper(),)
        cond.append(JobEvent.category.in_(cats))
    if actor_id:
        cond.append(JobEvent.actor_id == actor_id)
    if event_type:
        cond.append(JobEvent.event_type == event_type.upper())
    if result:
        cond.append(JobEvent.result == result.upper())
    if date_from:
        cond.append(JobEvent.created_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        cond.append(JobEvent.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(JobEvent.description.ilike(like), JobEvent.related_id.ilike(like), JobEvent.reason.ilike(like), JobEvent.previous_value.ilike(like), JobEvent.new_value.ilike(like), JobEvent.actor_name.ilike(like), JobEvent.event_type.ilike(like)))
    return and_(*cond)


@router.get("/{job_number}/activity")
def activity(job_number: str, category: str | None = None, actor_id: int | None = None, event_type: str | None = None, q: str | None = Query(default=None, max_length=100), date_from: date | None = None,
             date_to: date | None = None, result: str | None = None, order: str = Query(default="desc", pattern="^(asc|desc)$"), limit: int = Query(default=30, ge=1, le=200), offset: int = Query(default=0, ge=0),
             user: User = Depends(permission_required("job.view_history")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    where = _activity_query(job, category, actor_id, event_type, q, date_from, date_to, result)
    total = db.scalar(select(func.count()).select_from(JobEvent).where(where)) or 0
    order_by = JobEvent.id.asc() if order == "asc" else JobEvent.id.desc()
    rows = db.scalars(select(JobEvent).where(where).order_by(order_by).limit(limit).offset(offset)).all()
    actors = db.execute(select(JobEvent.actor_id, JobEvent.actor_name).where(JobEvent.organization_id == job.organization_id, JobEvent.shipment_id == job.id, JobEvent.actor_id.is_not(None)).distinct()).all()
    return {"total": total, "items": [_event_out(e) for e in rows], "actors": [{"id": a, "name": n} for a, n in actors], "categories": list(CATEGORY_FILTERS), "limit": limit, "offset": offset}


@router.get("/{job_number}/activity/export")
def export_activity(job_number: str, category: str | None = None, q: str | None = None, user: User = Depends(permission_required("job.export_history")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    where = _activity_query(job, category, None, None, q, None, None, None)
    rows = db.scalars(select(JobEvent).where(where).order_by(JobEvent.id.asc()).limit(20000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Event ID", "Job Number", "Time (UTC)", "Category", "Event Type", "Description", "Previous", "New", "Actor", "Actor Type", "Source", "Reason", "Related Type", "Related ID", "Result", "Correlation ID", "Approval Ref"])
    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") else s
    for e in rows:
        w.writerow([e.id, e.job_number, _ts(e.created_at), e.category, e.event_type, safe(e.description), safe(e.previous_value), safe(e.new_value), safe(e.actor_name), e.actor_type, e.source, safe(e.reason), e.related_type, e.related_id, e.result, e.correlation_id, e.approval_ref])
    jl.log_event(db, job, user, "HISTORY_EXPORTED", "SECURITY", f"Activity history exported ({len(rows)} events)", detail={"rows": len(rows), "category": category, "q": q})
    db.commit()
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{job.job_number}-activity.csv"'})
