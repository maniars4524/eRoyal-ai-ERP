"""Job stage derivation, closure checklist and the append-only job activity log.

Rules this module enforces (the frontend never decides any of them):
* Job Stage and Closure Status are separate. Nothing here ever sets a job to CLOSED automatically;
  a job may become READY FOR CLOSURE (a stage), and only an authorised user's explicit close action closes it.
* Stages come from ACTUAL milestones (JobMilestone rows, a confirmed booking, posted invoices ...), never from planned dates.
* A manual stage change never creates or completes a milestone. A later, higher-ranked validated event supersedes a manual stage
  (recorded in history with its trigger); the manual change itself is never erased.
* Every business change writes a JobEvent in the same transaction.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Booking, Document, JobCharge, JobEvent, JobMilestone, JobStageHistory, MasterRecord, Shipment, User

STAGES = ["Draft", "Confirmed", "On Transit", "Closed", "Reopened"]
RANK = {"Draft": 0, "Confirmed": 1, "On Transit": 2, "Reopened": 3, "Closed": 3}
MANUAL_STAGES = ["Draft", "Confirmed", "On Transit"]   # Closed and Reopened come only from the Close / Reopen actions
# Activities (milestones) are confirmed in the Tracking tab; those that show the cargo is moving put the job On Transit.
STAGE_MILESTONE = {
    "Job started": "booking_started", "Job confirmed": "booking_confirmed", "Documentation In Progress": "documentation_started", "Documentation Completed": "documentation_completed",
    "Cargo Received": "cargo_received", "Customs In Progress": "customs_started", "Customs Cleared": "customs_cleared", "Departed / Sailed": "departed", "In Transit": "in_transit",
    "Arrived": "arrived", "Delivery In Progress": "delivery_started", "Delivered": "delivered",
}
TRANSIT_MILESTONES = {"departed", "in_transit", "arrived", "delivery_started", "delivered"}
MILESTONES = {code: ('On Transit' if code in TRANSIT_MILESTONES else 'Confirmed') for code in STAGE_MILESTONE.values()}
MILESTONE_LABEL = {**{c: s for s, c in STAGE_MILESTONE.items()}, "empty_returned": "Empty container returned"}
ALL_MILESTONES = list(STAGE_MILESTONE.values()) + ["empty_returned"]
CATEGORIES = ("JOB", "HEADER", "OPERATIONS", "DOCUMENTS", "BUYCOST", "SELLCOST", "FINANCE", "STAGE", "CLOSURE", "REOPEN", "APPROVAL", "SECURITY", "SYSTEM")


def required_milestones(job: Shipment) -> list[str]:
    """Milestones that must be confirmed before the operational work counts as complete (configuration by mode and direction)."""
    mode, direction = job.mode, job.direction
    fcl = (job.container_mode or "").upper() == "FCL"
    if mode == "Sea" and direction == "Export":
        return ["documentation_completed", "cargo_received", "customs_cleared", "departed"]
    if mode == "Sea" and direction == "Import":
        return ["documentation_completed", "arrived", "customs_cleared", "delivered"] + (["empty_returned"] if fcl else [])
    if mode == "Air" and direction == "Export":
        return ["documentation_completed", "cargo_received", "customs_cleared", "departed"]
    if mode == "Air" and direction == "Import":
        return ["documentation_completed", "arrived", "customs_cleared", "delivered"]
    if direction in ("Cross-Trade", "Cross Trade"):
        return ["departed", "arrived", "delivered"]
    return ["delivered"]


def applicable_milestones(job: Shipment) -> list[str]:
    """Required ones plus the optional ones that can still be recorded (and move the stage) for this job."""
    req = required_milestones(job)
    optional = [c for c in ALL_MILESTONES if c not in req and not (c == "empty_returned" and (job.container_mode or "").upper() != "FCL")]
    return req + optional


def now() -> datetime:
    return datetime.now(timezone.utc)


def display_name(user: User | None) -> str:
    if user is None:
        return "System"
    return (f"{user.first_name} {user.last_name}".strip() or user.username)


def actor_fields(user: User | None, actor_type: str | None = None) -> dict:
    if user is None:
        return {"actor_type": actor_type or "SYSTEM", "actor_id": None, "actor_name": "System"}
    return {"actor_type": actor_type or "USER", "actor_id": user.id, "actor_name": display_name(user)}


def log_event(db: Session, job: Shipment, user: User | None, event_type: str, category: str, description: str, *, previous=None, new=None, reason=None, related_type=None, related_id=None,
              correlation_id=None, approval_ref=None, result="SUCCESS", source="API", detail=None, idempotency_key=None, actor_type=None) -> JobEvent:
    ev = JobEvent(organization_id=job.organization_id, shipment_id=job.id, job_number=job.job_number, event_type=event_type, category=category, description=description,
                  previous_value=None if previous is None else str(previous), new_value=None if new is None else str(new), reason=reason, related_type=related_type,
                  related_id=None if related_id is None else str(related_id), correlation_id=correlation_id or uuid.uuid4().hex[:16], approval_ref=approval_ref, result=result, source=source,
                  detail=detail or {}, idempotency_key=idempotency_key, **actor_fields(user, actor_type))
    db.add(ev)
    job.last_activity_at = now()
    db.flush()
    return ev


def set_stage(db: Session, job: Shipment, new_stage: str, source: str, user: User | None, *, trigger=None, reason=None, related_ref=None, approval_ref=None, correlation_id=None) -> bool:
    """Change the stage and write the stage history + activity event. Returns False when nothing changed (no duplicate transitions)."""
    if job.job_stage == new_stage:
        return False
    previous = job.job_stage
    ev = log_event(db, job, user, "STAGE_CHANGED_MANUAL" if source == "MANUAL" else "STAGE_CHANGED_AUTO", "STAGE", f"Job stage changed from {previous} to {new_stage}", previous=previous, new=new_stage, reason=reason,
                   related_type="MILESTONE" if (trigger or "").startswith("MILESTONE") else None, related_id=related_ref, correlation_id=correlation_id, approval_ref=approval_ref, detail={"trigger": trigger, "source": source},
                   actor_type=None if source == "MANUAL" or user else "SYSTEM")
    db.add(JobStageHistory(organization_id=job.organization_id, shipment_id=job.id, job_number=job.job_number, previous_stage=previous, new_stage=new_stage, source=source, trigger=trigger, reason=reason,
                           actor_id=user.id if user else None, actor_name=display_name(user), related_ref=related_ref, approval_ref=approval_ref, event_id=ev.id))
    job.job_stage, job.stage_source, job.stage_reason, job.stage_updated_at = new_stage, source, reason, now()
    return True


# ------------------------------------------------------------------------------------------------ financial data
def money_totals(db: Session, job: Shipment) -> dict:
    from .job_charges_api import line_out, totals  # local import: job_charges_api imports this module

    rows = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id).order_by(JobCharge.line_no)))
    lines = [line_out(r) for r in rows]
    t = totals(lines)
    sell_lines = [r for r in rows if (r.sell_rate or 0) > 0]
    buy_lines = [r for r in rows if (r.buy_rate or 0) > 0]
    return {"sell": Decimal(t["ar"]), "sell_tax": Decimal(t["ar_tax"]), "sell_total": Decimal(t["ar_total"]), "buy": Decimal(t["ap"]), "buy_total": Decimal(t["ap_total"]), "margin": Decimal(t["gross_profit"]),
            "sell_lines": len(sell_lines), "buy_lines": len(buy_lines)}


def _posted(db: Session, job: Shipment, doc_type: str) -> Decimal:
    return db.scalar(select(func.coalesce(func.sum(Document.grand_total), 0)).where(Document.organization_id == job.organization_id, Document.shipment_id == job.id, Document.status == "POSTED", Document.doc_type == doc_type)) or Decimal(0)


def milestone_map(db: Session, job: Shipment) -> dict[str, JobMilestone]:
    got = {m.code: m for m in db.scalars(select(JobMilestone).where(JobMilestone.shipment_id == job.id))}
    booking = db.scalar(select(Booking).where(Booking.organization_id == job.organization_id, Booking.job_number == job.job_number).order_by(Booking.id.desc()))
    if booking is not None:
        ts = booking.created_at
        if "booking_started" not in got:
            got["booking_started"] = JobMilestone(code="booking_started", actual_at=ts, remarks="From booking " + (booking.number or "draft"))
        if booking.status == "CONFIRMED" and "booking_confirmed" not in got:
            got["booking_confirmed"] = JobMilestone(code="booking_confirmed", actual_at=booking.updated_at or ts, remarks="Booking " + (booking.number or "") + " confirmed")
    return got


def checklist(db: Session, job: Shipment, user: User | None, ack: dict | None = None) -> dict:
    """The closure checklist, derived from this job's mode/type and the live accounting data."""
    ack = ack or {}
    ms = milestone_map(db, job)
    req = required_milestones(job)
    mt = money_totals(db, job)
    inv, pinv = _posted(db, job, "invoice") + _posted(db, job, "debit_note"), _posted(db, job, "purchase_invoice")
    items: list[dict] = []

    def add(key, group, label, passed, detail="", *, applicable=True, ack_key=None, link=None):
        if not applicable:
            items.append({"key": key, "group": group, "label": label, "status": "NA", "blocking": False, "detail": "Not applicable to this job", "link": link})
            return
        if passed:
            items.append({"key": key, "group": group, "label": label, "status": "PASS", "blocking": False, "detail": detail, "link": link})
        elif ack_key and ack.get(ack_key):
            items.append({"key": key, "group": group, "label": label, "status": "ACK", "blocking": False, "detail": detail + " (acknowledged by the closing user)", "link": link})
        else:
            items.append({"key": key, "group": group, "label": label, "status": "FAIL", "blocking": True, "detail": detail, "ack_key": ack_key, "link": link})

    add("docs_complete", "A. Documentation", "Mandatory shipment documents completed", "documentation_completed" in ms, "Record the 'Documentation completed' milestone once all mandatory documents are done", applicable="documentation_completed" in req, link="job-detail:jt7")
    add("bl_awb", "A. Documentation", "BL / AWB status updated", bool((job.bl_awb or "").strip()), "Enter the BL / AWB number", applicable=job.mode in ("Sea", "Air"), link="job-detail:jt3")
    add("sell_entered", "B. Sell Cost", "Selling charges entered", mt["sell_lines"] > 0, "No sell charge with a rate exists on this job", link="job-detail:jt5")
    inv_ok = mt["sell_total"] > 0 and inv >= mt["sell_total"] - Decimal("0.01")
    add("sell_invoiced", "B. Sell Cost", "Customer invoices generated and posted for the sell cost", inv_ok, f"Posted invoices {inv} against sell cost {mt['sell_total']}", link="job-detail:jt8")
    add("buy_entered", "C. Buy Cost", "Buying charges entered", mt["buy_lines"] > 0, "No buy charge with a rate exists on this job. Acknowledge only if the job genuinely has no buy cost", ack_key="no_buy_cost", link="job-detail:jt5")
    pinv_ok = mt["buy_total"] > 0 and pinv >= mt["buy_total"] - Decimal("0.01")
    add("vendor_invoiced", "C. Buy Cost", "Vendor purchase invoices recorded (or accrual accepted)", pinv_ok or mt["buy_total"] == 0, f"Posted purchase invoices {pinv} against buy cost {mt['buy_total']}. Acknowledge to close under the approved accrual process", applicable=True, ack_key="accrual", link="job-detail:jt8")
    add("profit_calc", "D. Financial Verification", "Job profitability calculated", mt["sell_lines"] > 0 or mt["buy_lines"] > 0, "Enter sell and buy charges so profitability can be calculated", link="job-detail:jt9")
    add("cost_sheet_review", "D. Financial Verification", "Buy / Sell cost sheet reviewed", False, "Review the cost sheet and confirm it", ack_key="cost_sheet_reviewed", link="job-detail:jt9")
    diff = abs(inv - mt["sell_total"]) if inv_ok is False and inv > 0 else Decimal(0)
    add("no_discrepancy", "D. Financial Verification", "No unresolved financial discrepancy", diff < Decimal("1"), f"Posted invoices differ from the sell cost sheet by {diff}", ack_key="discrepancy", link="job-detail:jt8")
    for code in req:
        if code == "documentation_completed":
            continue
        add("ms_" + code, "E. Operational Completion", MILESTONE_LABEL.get(code, code) + " confirmed", code in ms, "Confirm the actual date in the Tracking tab", link="job-detail:jt6")
    perm = user is None or user.is_super_admin or any(p.code == "job.close" for r in user.roles for p in r.permissions)
    add("authorized", "F. Closure Authorization", "User holds the Close Job permission", perm, "You do not have permission to close jobs")
    blocking = [i for i in items if i["blocking"]]
    return {"items": items, "blocking": blocking, "ready": not blocking, "totals": {k: str(v) for k, v in mt.items() if k in ("sell", "sell_total", "buy", "buy_total", "margin")}, "posted_invoices": str(inv), "posted_purchase_invoices": str(pinv)}


def operationally_complete(db: Session, job: Shipment) -> bool:
    ms = milestone_map(db, job)
    return all(code in ms for code in required_milestones(job))


def derive_stage(db: Session, job: Shipment) -> tuple[str, str]:
    """Return (stage, trigger) from the job's actual data. Closed / Reopened follow the closure status; otherwise Draft -> Confirmed -> On Transit."""
    if job.closure_status == "CLOSED":
        return "Closed", "CLOSED"
    if job.closure_status == "REOPENED":
        return "Reopened", "REOPENED"
    ms = milestone_map(db, job)
    transit = sorted(c for c in ms if c in TRANSIT_MILESTONES)
    if transit:
        return "On Transit", "MILESTONE:" + transit[-1]
    if job.booking_id or (job.status or "Draft") != "Draft" or "booking_confirmed" in ms:
        return "Confirmed", "JOB_CONFIRMED"
    return "Draft", "JOB_CREATED"


def recompute_stage(db: Session, job: Shipment, trigger: str, user: User | None = None, correlation_id=None) -> bool:
    """Automatic stage progression. Never touches the closure status; frozen while the job is CLOSED."""
    if job.closure_status == "CLOSED":
        return False
    stage, why = derive_stage(db, job)
    if job.closure_status == "REOPENED" or stage in ("Closed", "Reopened"):
        return set_stage(db, job, stage, "AUTO", user, trigger=trigger[:170], correlation_id=correlation_id)
    if stage == job.job_stage:
        return False
    if job.stage_source == "MANUAL" and RANK[stage] <= RANK.get(job.job_stage, 0):
        return False  # a manual stage stands until a higher-ranked validated event supersedes it
    superseding = job.stage_source == "MANUAL"
    reason_text = trigger if why.startswith("MILESTONE:") else f"{trigger} ({why})"
    return set_stage(db, job, stage, "AUTO", user, trigger=reason_text[:170] + (" - supersedes manual stage" if superseding else ""), related_ref=why.split(":", 1)[1] if why.startswith("MILESTONE:") else None, correlation_id=correlation_id)


def assert_job_editable(job: Shipment, what: str = "this job") -> None:
    if job.closure_status == "CLOSED":
        raise HTTPException(status_code=409, detail=f"Job {job.job_number} is closed. Reopen it (with a reason) before changing {what}.")


def setting_enabled(db: Session, org_id: int, code: str) -> bool:
    row = db.scalar(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "setting", MasterRecord.code == code, MasterRecord.status == "ACTIVE"))
    return bool(row and str((row.data or {}).get("enabled", "")).lower() in ("yes", "true", "1"))


def on_job_created(db: Session, job: Shipment, user: User | None, via: str, detail: dict | None = None) -> None:
    """Call right after a job row is flushed: writes the first event and stage record."""
    job.stage_updated_at = now()
    corr = uuid.uuid4().hex[:16]
    ev = log_event(db, job, user, "JOB_CREATED", "JOB", f"Job {job.job_number} created ({via})", new=job.job_number, correlation_id=corr, source=via.upper().replace(" ", "_")[:20],
                   detail={"mode": job.mode, "direction": job.direction, "container_mode": job.container_mode, "job_type": job.job_type, **(detail or {})})
    db.add(JobStageHistory(organization_id=job.organization_id, shipment_id=job.id, job_number=job.job_number, previous_stage=None, new_stage=job.job_stage or "Draft", source="AUTO", trigger="JOB_CREATED",
                           actor_id=user.id if user else None, actor_name=display_name(user), event_id=ev.id))
    recompute_stage(db, job, "JOB_CREATED", user, corr)


def doc_hook(db: Session, doc, user: User | None, action: str) -> None:
    """Activity + stage refresh for invoices / purchase invoices / notes that belong to a job."""
    if not doc.shipment_id:
        return
    job = db.get(Shipment, doc.shipment_id)
    if job is None:
        return
    category = {"invoice": "SELLCOST", "debit_note": "SELLCOST", "purchase_invoice": "BUYCOST"}.get(doc.doc_type, "FINANCE")
    label = doc.doc_type.replace("_", " ")
    log_event(db, job, user, f"{doc.doc_type.upper()}_{action}", category, f"{label.capitalize()} {doc.doc_number or '(draft)'} {action.lower().replace('_', ' ')}", new=str(doc.grand_total), related_type="DOCUMENT", related_id=doc.id,
              detail={"doc_type": doc.doc_type, "number": doc.doc_number, "total": str(doc.grand_total), "party": doc.party_name})
    if action in ("POSTED", "CANCELLED"):
        recompute_stage(db, job, f"{doc.doc_type.upper()}_{action}", user)


def guard_document_job(db: Session, shipment_id: int | None) -> None:
    if shipment_id:
        job = db.get(Shipment, shipment_id)
        if job is not None:
            assert_job_editable(job, "its invoices")
