from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Shipment, User
from .schemas_auth import ShipmentCreate, ShipmentUpdate

router = APIRouter(prefix="/api/shipments", tags=["shipments"])

MODE_CODES = {"Sea": "SEA", "Air": "AIR", "Road": "ROD", "Rail": "RAI"}
DIRECTION_CODES = {"Export": "EXP", "Import": "IMP", "Domestic": "DOM", "Cross-Trade": "XTR"}


def _require_tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jobs are tenant-scoped; Super Admin does not manage shipment jobs directly")
    return user.organization_id


def _next_job_number(db: Session, organization_id: int, mode: str, direction: str) -> str:
    prefix = MODE_CODES.get(mode, "GEN") + DIRECTION_CODES.get(direction, "GEN")
    year = str(datetime.now(timezone.utc).year)[-2:]
    count = db.scalar(select(func.count()).select_from(Shipment).where(Shipment.organization_id == organization_id)) or 0
    for attempt in range(count + 1, count + 50):
        candidate = f"{prefix}-{year}-{attempt:06d}"
        if not db.scalar(select(Shipment.id).where(Shipment.job_number == candidate)):
            return candidate
    raise HTTPException(status_code=500, detail="Could not allocate a job number")


@router.get("")
def list_shipments_full(user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    return db.scalars(select(Shipment).where(Shipment.organization_id == organization_id).order_by(Shipment.created_at.desc())).all()


@router.post("", status_code=201)
def create_shipment(payload: ShipmentCreate, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    data = payload.model_dump()
    job_number = _next_job_number(db, organization_id, data["mode"], data["direction"])
    shipment = Shipment(organization_id=organization_id, job_number=job_number, created_by=user.id, **data)
    db.add(shipment)
    try:
        db.flush()
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
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(shipment, key, value)
    record_audit(db, "SHIPMENT_UPDATED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), changes)
    db.commit()
    db.refresh(shipment)
    return shipment


@router.delete("/{shipment_id}", status_code=204)
def delete_shipment(shipment_id: int, user: User = Depends(permission_required("shipment.delete")), db: Session = Depends(get_db)):
    organization_id = _require_tenant(user)
    shipment = db.scalar(select(Shipment).where(Shipment.id == shipment_id, Shipment.organization_id == organization_id))
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    record_audit(db, "SHIPMENT_DELETED", "SHIPMENT", user, organization_id, "Shipment", str(shipment.id), {"job_number": shipment.job_number})
    db.delete(shipment)
    db.commit()
    return None
