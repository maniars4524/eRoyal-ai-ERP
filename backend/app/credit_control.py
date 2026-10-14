"""Credit exposure and enforcement for credit customers (backend rules; the UI only reflects them).

Available credit = effective limit - current exposure.
Exposure = posted receivables outstanding (invoices/debit notes net of credit notes and receipts applied)
         + unbilled job charges (sell side incl. tax, minus what is already on posted invoices of that job)
         - unapplied receipts of the customer.
Nothing is counted twice: invoiced amounts leave "unbilled" the moment they are posted and appear once as receivables.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .billing import outstanding
from .job_charges_api import line_out, totals
from .models import CreditEvent, CreditFacility, CreditRequest, Customer, Document, JobCharge, OrgBranch, Payment, PaymentAllocation, Shipment

ZERO = Decimal(0)
AR_TYPES = ("invoice", "debit_note")
DEFAULT_NOTIFY_DAYS = [30, 15, 7, 0]


def today() -> date:
    return datetime.now(timezone.utc).date()


def is_credit_customer(customer: Customer) -> bool:
    return str((customer.data or {}).get("credit_customer", "")).strip().lower() in ("yes", "true", "1")


def notify_days(customer: Customer) -> list[int]:
    raw = str((customer.data or {}).get("credit_notify_days", "") or "")
    days = sorted({int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}, reverse=True)
    return days or DEFAULT_NOTIFY_DAYS


def event(db: Session, org_id: int, customer_id: int, action: str, user_id: int | None, facility_id: int | None = None, request_id: int | None = None, detail: dict | None = None) -> None:
    db.add(CreditEvent(organization_id=org_id, customer_id=customer_id, facility_id=facility_id, request_id=request_id, action=action, user_id=user_id, detail=detail or {}))


def active_facility(db: Session, customer_id: int, on: date | None = None) -> CreditFacility | None:
    on = on or today()
    rows = db.scalars(select(CreditFacility).where(CreditFacility.customer_id == customer_id, CreditFacility.status == "APPROVED", CreditFacility.start_date <= on, CreditFacility.expiry_date >= on)
                      .order_by(CreditFacility.start_date.desc(), CreditFacility.id.desc())).all()
    return rows[0] if rows else None


def latest_approved(db: Session, customer_id: int) -> CreditFacility | None:
    return db.scalars(select(CreditFacility).where(CreditFacility.customer_id == customer_id, CreditFacility.status.in_(("APPROVED", "SUPERSEDED")), CreditFacility.approved_at.is_not(None))
                      .order_by(CreditFacility.expiry_date.desc(), CreditFacility.id.desc())).first()


def exposure(db: Session, org_id: int, customer: Customer, branch_id: int | None = None) -> dict:
    on = today()
    docs = db.scalars(select(Document).where(Document.organization_id == org_id, Document.customer_id == customer.id, Document.status == "POSTED", Document.doc_type.in_(AR_TYPES))).all()
    if branch_id is not None:
        docs = [d for d in docs if str((d.header or {}).get("party_branch_id", "")) == str(branch_id)]
    receivables = sum((max(outstanding(d), ZERO) for d in docs), ZERO)
    overdue = sum((max(outstanding(d), ZERO) for d in docs if d.due_date and d.due_date < on), ZERO)
    jobs = db.scalars(select(Shipment).where(Shipment.organization_id == org_id, Shipment.customer_id == customer.id)).all()
    if branch_id is not None:
        jobs = [j for j in jobs if j.party_branch_id == branch_id]
    unbilled = ZERO
    if jobs:
        ids = [j.id for j in jobs]
        rows = db.scalars(select(JobCharge).where(JobCharge.shipment_id.in_(ids))).all()
        sell_by_job: dict[int, Decimal] = {}
        for sid in ids:
            lines = [line_out(r) for r in rows if r.shipment_id == sid]
            job = next(j for j in jobs if j.id == sid)
            sell_by_job[sid] = Decimal(totals(lines)["ar_total"]) if lines else Decimal(str(job.revenue or 0))  # no charge lines yet: use the job's expected revenue
        invoiced: dict[int, Decimal] = {}
        for d in db.scalars(select(Document).where(Document.organization_id == org_id, Document.shipment_id.in_(ids), Document.status == "POSTED", Document.doc_type.in_(AR_TYPES))).all():
            invoiced[d.shipment_id] = invoiced.get(d.shipment_id, ZERO) + d.grand_total
        unbilled = sum((max(sell_by_job[s] - invoiced.get(s, ZERO), ZERO) for s in ids), ZERO)
    unapplied = ZERO
    if branch_id is None:
        for p in db.scalars(select(Payment).where(Payment.organization_id == org_id, Payment.direction == "RECEIPT", Payment.status == "POSTED", Payment.party_name == customer.name)).all():
            allocated = sum((a.amount for a in p.allocations), ZERO)
            unapplied += max(p.amount - allocated, ZERO)
    total = max(receivables + unbilled - unapplied, ZERO)
    return {"receivables": receivables, "overdue": overdue, "unbilled": unbilled, "unapplied_receipts": unapplied, "exposure": total}


def _valid(req: CreditRequest, on: date) -> bool:
    return req.status == "APPROVED" and (req.valid_until is None or req.valid_until >= on)


def status_for(db: Session, org_id: int, customer: Customer, branch_id: int | None = None, add: Decimal = ZERO) -> dict:
    on = today()
    if not is_credit_customer(customer):
        return {"applicable": False, "state": "NOT_CREDIT", "message": "Non-credit customer (advance payment process)"}
    fac = active_facility(db, customer.id, on)
    last = latest_approved(db, customer.id)
    if not fac and not last:
        return {"applicable": False, "state": "NO_FACILITY", "message": "Credit facility not approved yet (advance payment process applies)"}
    reqs = db.scalars(select(CreditRequest).where(CreditRequest.customer_id == customer.id)).all()
    policy = (fac or last).policy
    ex = exposure(db, org_id, customer)
    extension = sum((r.amount or ZERO for r in reqs if r.kind == "EXTENSION" and _valid(r, on) and (not fac or r.facility_id in (None, fac.id))), ZERO)
    graces = [r for r in reqs if r.kind == "GRACE" and _valid(r, on)]
    overrides = [r for r in reqs if r.kind == "OVERRIDE" and _valid(r, on) and (r.branch_id in (None, branch_id))]
    releases = [r for r in reqs if r.kind == "RELEASE" and _valid(r, on)]
    out = {"applicable": True, "policy": policy, "facility_id": (fac or last).id, "currency": (fac or last).currency, "credit_days": (fac or last).credit_days, "expiry_date": (fac or last).expiry_date,
           "exposure": ex, "extension": extension, "grace_active": bool(graces), "override_available": bool(overrides), "release_available": bool(releases), "projected_add": add}
    if not fac:
        out.update({"state": "EXPIRED", "limit": ZERO, "effective_limit": ZERO, "available": ZERO, "excess": ex["exposure"] + add,
                    "message": f"Credit facility expired on {last.expiry_date}. Renewal or authorised release required."})
        return out
    limit = fac.approved_limit or ZERO
    effective = limit + extension
    projected = ex["exposure"] + add
    available = effective - ex["exposure"]
    out.update({"limit": limit, "effective_limit": effective, "available": available, "excess": max(projected - effective, ZERO), "state": "OK", "message": "Within credit limit"})
    if projected > effective:
        out.update({"state": "EXCEEDED", "message": f"Credit limit exceeded: exposure {projected} against limit {effective}"})
    sub = next((b for b in (fac.branch_limits or []) if branch_id is not None and int(b.get("branch_id", 0)) == branch_id), None)
    if sub and out["state"] == "OK":
        bex = exposure(db, org_id, customer, branch_id)["exposure"] + add
        out["branch"] = {"branch_id": branch_id, "limit": Decimal(str(sub["limit"])), "exposure": bex}
        if bex > Decimal(str(sub["limit"])):
            out.update({"state": "BRANCH_EXCEEDED", "excess": bex - Decimal(str(sub["limit"])), "message": f"Branch sub-limit exceeded: exposure {bex} against sub-limit {sub['limit']}"})
    out["_graces"], out["_overrides"], out["_releases"] = graces, overrides, releases
    return out


def enforce(db: Session, org_id: int, customer_id: int | None, action: str, user_id: int | None, branch_id: int | None = None, add: Decimal = ZERO) -> None:
    """Raise 409 when the customer's credit situation does not allow `action`, unless an approved allowance covers it."""
    if not customer_id:
        return
    customer = db.get(Customer, customer_id)
    if not customer or customer.organization_id != org_id:
        return
    st = status_for(db, org_id, customer, branch_id, add)
    if not st["applicable"] or st["state"] == "OK":
        return
    on = today()
    projected = st["exposure"]["exposure"] + add
    graces = st.get("_graces", [])
    if st["state"] == "EXPIRED":
        if graces:
            return
    else:
        for g in graces:
            if projected <= st["effective_limit"] + (g.amount or ZERO):
                return
    if st["policy"] == "SOFT":
        for o in st.get("_overrides", []):
            if o.one_time:
                o.status = "USED"
                event(db, org_id, customer.id, "OVERRIDE_USED", user_id, o.facility_id, o.id, {"action": action, "amount": str(add)})
            return
    else:
        if st.get("_releases"):
            return
    event(db, org_id, customer.id, "ACTION_BLOCKED", user_id, st.get("facility_id"), None, {"action": action, "state": st["state"], "policy": st["policy"], "amount": str(add)})
    db.commit()
    kind = "Hard block" if st["policy"] == "HARD" else "Soft block (an authorised override is required)"
    raise HTTPException(status_code=409, detail=f"CREDIT_BLOCK: {customer.name}: {st['message']}. {kind}. Action: {action}.")


def expiry_alerts(db: Session, org_id: int) -> list[dict]:
    on = today()
    out = []
    for fac in db.scalars(select(CreditFacility).where(CreditFacility.organization_id == org_id, CreditFacility.status == "APPROVED", CreditFacility.expiry_date.is_not(None))).all():
        cust = db.get(Customer, fac.customer_id)
        if not cust or not is_credit_customer(cust):
            continue
        left = (fac.expiry_date - on).days
        marks = notify_days(cust)
        if left <= max(marks):
            bucket = min((m for m in marks if m >= left), default=0)
            out.append({"customer_id": cust.id, "customer": cust.name, "facility_id": fac.id, "expiry_date": fac.expiry_date.isoformat(), "days_left": left, "interval": bucket, "expired": left < 0})
    return sorted(out, key=lambda x: x["days_left"])
