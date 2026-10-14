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


@router.get("")
def list_shipments_full(user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    return db.scalars(select(Shipment).where(Shipment.organization_id == organization_id).order_by(Shipment.created_at.desc())).all()


@router.post("", status_code=201)
def create_shipment(payload: ShipmentCreate, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    data = payload.model_dump()
    if data.get("party_branch_id") is not None and not (data.get("customer_id") and db.scalar(select(OrgBranch.id).where(OrgBranch.id == data["party_branch_id"], OrgBranch.customer_id == data["customer_id"], OrgBranch.organization_id == organization_id))):
        raise HTTPException(status_code=422, detail="The branch does not belong to the selected organization")
    credit_enforce(db, organization_id, data.get("customer_id"), "create shipment job", user.id, data.get("party_branch_id"), Decimal(str(data.get("revenue") or 0)))
    job_number = _next_job_number(db, organization_id, data["mode"], data["direction"], data.get("container_mode"), user)
    shipment = Shipment(organization_id=organization_id, job_number=job_number, created_by=user.id, **data)
    db.add(shipment)
    try:
        db.flush()
        jl.on_job_created(db, shipment, user, "manual entry")
        record_audit(db, "SHIPMENT_CREATED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), {"job_number": job_number})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create shipment job — please retry")
    db.refresh(shipment)
    return shipment


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
