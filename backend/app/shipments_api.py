from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from decimal import Decimal
from . import job_lifecycle as jl
from .credit_control import enforce as credit_enforce
from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import DocSequence, Shipment, User, OrgBranch
from .schemas_auth import ShipmentCreate, ShipmentUpdate

router = APIRouter(prefix="/api/shipments", tags=["shipments"])

MODE_CODES = {"Sea": "SEA", "Air": "AIR", "Road": "ROD", "Rail": "RAI"}
DIRECTION_CODES = {"Export": "EXP", "Import": "IMP", "Domestic": "DOM", "Cross-Trade": "XTR"}


def _require_tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jobs are tenant-scoped; Super Admin does not manage shipment jobs directly")
    return user.organization_id


def job_shipment_key(mode: str, direction: str, container_mode: str | None = None) -> str:
    key = f"{mode} {direction}"
    return key + f" {container_mode}" if mode == "Sea" and container_mode in ("FCL", "LCL") else key


def _next_job_number(db: Session, organization_id: int, mode: str, direction: str, container_mode: str | None = None, user=None) -> str:
    """Job numbers come from the company's active Job Number sequence (Settings -> Sequence Setting), resolved by shipment type, branch and financial year."""
    from .sequences import issue

    return issue(db, organization_id, "job", user=user, shipment_key=job_shipment_key(mode, direction, container_mode))


def _next_cha_number(db: Session, organization_id: int, mode: str, direction: str, user=None) -> str:
    """CHA (customs clearance) jobs have a numbering of their own: SE/CC/001 (Sea Export), SI/CC/001, AE/CC/001, AI/CC/001 ..., separate from the freight job numbers."""
    from .sequences import issue

    return issue(db, organization_id, "cha_job", user=user, shipment_key=f"{mode} {direction}")


@router.get("")
def list_shipments_full(user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    return db.scalars(select(Shipment).where(Shipment.organization_id == organization_id).order_by(Shipment.created_at.desc())).all()


@router.post("", status_code=201)
def create_shipment(payload: ShipmentCreate, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    data = payload.model_dump()
    if data.get("job_type") == "Customs":
        raise HTTPException(status_code=422, detail="CHA (customs) jobs are created in the CHA module (Logistics & Customs > CHA > Create Job), from a booking number")
    if data.get("party_branch_id") is not None and not (data.get("customer_id") and db.scalar(select(OrgBranch.id).where(OrgBranch.id == data["party_branch_id"], OrgBranch.customer_id == data["customer_id"], OrgBranch.organization_id == organization_id))):
        raise HTTPException(status_code=422, detail="The branch does not belong to the selected organization")
    credit_enforce(db, organization_id, data.get("customer_id"), "create shipment job", user.id, data.get("party_branch_id"), Decimal(str(data.get("revenue") or 0)))
    job_number = _next_job_number(db, organization_id, data["mode"], data["direction"], data.get("container_mode"), user)
    shipment = Shipment(organization_id=organization_id, job_number=job_number, created_by=user.id, **data)
    db.add(shipment)
    try:
        db.flush()
        jl.on_job_created(db, shipment, user, "manual entry")
        if shipment.job_type == "Forwarding" and not shipment.booking_id:
            from .bookings_api import auto_booking

            auto_booking(db, organization_id, user, shipment, "Freight")          # a direct freight job still carries a (system generated) booking number
        record_audit(db, "SHIPMENT_CREATED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), {"job_number": job_number})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create shipment job — please retry")
    db.refresh(shipment)
    return shipment


@router.post("/by-number/{job_number:path}/duplicate", status_code=201)
def duplicate_shipment_by_number(job_number: str, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    src = db.scalar(select(Shipment).where(Shipment.job_number == job_number, Shipment.organization_id == organization_id))
    if not src:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return duplicate_shipment(src.id, user, db)


@router.post("/{shipment_id}/duplicate", status_code=201)
def duplicate_shipment(shipment_id: int, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    """A new job (the next running job number, a fresh Draft) with the details and the charge lines of an existing one, whatever its stage. Its documents, containers, bills and booking link are not copied."""
    from .models import JobCharge

    organization_id = _require_tenant(user)
    src = db.scalar(select(Shipment).where(Shipment.id == shipment_id, Shipment.organization_id == organization_id))
    if not src:
        raise HTTPException(status_code=404, detail="Shipment not found")
    credit_enforce(db, organization_id, src.customer_id, "create shipment job", user.id, src.party_branch_id, Decimal(0))
    job_number = _next_job_number(db, organization_id, src.mode, src.direction, src.container_mode, user)
    copy = Shipment(organization_id=organization_id, job_number=job_number, created_by=user.id, job_type=src.job_type, mode=src.mode, direction=src.direction, container_mode=src.container_mode, department=src.department,
                    pol=src.pol, pod=src.pod, etd=src.etd, eta=src.eta, customer_id=src.customer_id, party_branch_id=src.party_branch_id, branch=src.branch, salesperson=src.salesperson, consignee=src.consignee,
                    packages=src.packages, package_type=src.package_type, gross_weight=src.gross_weight, volume=src.volume, commodity=src.commodity, revenue=0)
    db.add(copy)
    try:
        db.flush()
        for i, c in enumerate(db.scalars(select(JobCharge).where(JobCharge.shipment_id == src.id, JobCharge.organization_id == organization_id).order_by(JobCharge.line_no)), start=1):
            db.add(JobCharge(organization_id=organization_id, shipment_id=copy.id, line_no=i, service=c.service, charge_name=c.charge_name, description=c.description, uom=c.uom, qty=c.qty, hsn=c.hsn,
                             buy_ccy=c.buy_ccy, buy_exch=c.buy_exch, buy_rate=c.buy_rate, buy_tax_type=c.buy_tax_type, buy_tax_pct=c.buy_tax_pct, vendor=c.vendor, use_vendor=c.use_vendor,
                             sell_ccy=c.sell_ccy, sell_exch=c.sell_exch, sell_rate=c.sell_rate, sell_tax_type=c.sell_tax_type, sell_tax_pct=c.sell_tax_pct, client=c.client, use_client=c.use_client, breakup=c.breakup))
        from .models import JobDetail

        det = db.scalar(select(JobDetail).where(JobDetail.shipment_id == src.id, JobDetail.organization_id == organization_id))
        if det is not None:     # parties, cargo, containers, air dimensions, BL and import details come along; the BL number, its stage and its release do not (they belong to the old BL)
            import copy as _copy

            bl = {k: v for k, v in _copy.deepcopy(det.bl or {}).items() if k not in ("hblNo", "hblDate", "blStage", "releaseType", "digitalSign", "releasedAt", "releaseOverride")}
            db.add(JobDetail(organization_id=organization_id, shipment_id=copy.id, parties=_copy.deepcopy(det.parties or {}), cargo=_copy.deepcopy(det.cargo or {}), containers=_copy.deepcopy(det.containers or []),
                             dims=_copy.deepcopy(det.dims or {}), bl=bl, impdocs=_copy.deepcopy(det.impdocs or {}), saved_sections=_copy.deepcopy(det.saved_sections or {}), version=1, updated_by=user.id))
        jl.on_job_created(db, copy, user, f"duplicate of {src.job_number}")
        record_audit(db, "SHIPMENT_DUPLICATED", "SHIPMENT", user, organization_id, "Shipment", str(copy.id), {"job_number": job_number, "from": src.job_number})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not duplicate the job — please retry")
    db.refresh(copy)
    return copy


@router.get("/{shipment_id}")
def get_shipment(shipment_id: int, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    shipment = db.scalar(select(Shipment).where(Shipment.id == shipment_id, Shipment.organization_id == organization_id))
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return shipment


@router.patch("/{shipment_id}")
def update_shipment(shipment_id: int, payload: ShipmentUpdate, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    shipment = db.scalar(select(Shipment).where(Shipment.id == shipment_id, Shipment.organization_id == organization_id))
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    jl.assert_job_editable(shipment, "job details")
    changes = payload.model_dump(exclude_unset=True)
    old = {k: getattr(shipment, k) for k in changes}
    for key, value in changes.items():
        setattr(shipment, key, value)
    corr = __import__("uuid").uuid4().hex[:16]
    for k, v in changes.items():
        if str(old[k] if old[k] is not None else "") != str(v if v is not None else ""):
            jl.log_event(db, shipment, user, "JOB_HEADER_CHANGED", "HEADER", f"{k.replace('_', ' ').capitalize()} changed", previous=old[k], new=v, correlation_id=corr, related_type="FIELD", related_id=k)
    jl.recompute_stage(db, shipment, "JOB_UPDATED", user, corr)
    record_audit(db, "SHIPMENT_UPDATED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), payload.model_dump(exclude_unset=True, mode="json"))
    db.commit()
    db.refresh(shipment)
    return shipment


@router.delete("/{shipment_id}", status_code=204)
def delete_shipment(shipment_id: int, user: User = Depends(permission_required("shipment.delete")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    shipment = db.scalar(select(Shipment).where(Shipment.id == shipment_id, Shipment.organization_id == organization_id))
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.closure_cycle > 0 or shipment.closure_status != "OPEN":
        raise HTTPException(status_code=409, detail="A job that has been closed (or reopened) is part of the permanent record and cannot be deleted")
    jl.log_event(db, shipment, user, "JOB_DELETED", "JOB", f"Job {shipment.job_number} deleted", previous=shipment.job_number)
    record_audit(db, "SHIPMENT_DELETED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), {"job_number": shipment.job_number})
    db.delete(shipment)
    db.commit()
    return None
