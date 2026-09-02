from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .auth_api import record_audit
from .db import get_db
from .models import Port, User
from .schemas_auth import PortCreate, PortUpdate

router = APIRouter(prefix="/api/ports", tags=["ports"])


def _require_super_admin(user: User) -> None:
    if not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Port Master is platform-managed by Super Admin")


@router.get("")
def list_ports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Port).order_by(Port.name)).all()


@router.post("", status_code=201)
def create_port(payload: PortCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    port = Port(created_by=user.id, **payload.model_dump())
    db.add(port)
    try:
        db.flush()
        record_audit(db, "PORT_CREATED", "PORT", user, None, "Port", str(port.id), {"code": port.code, "name": port.name})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A port with this code already exists")
    db.refresh(port)
    return port


@router.patch("/{port_id}")
def update_port(port_id: int, payload: PortUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    port = db.get(Port, port_id)
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(port, key, value)
    record_audit(db, "PORT_UPDATED", "PORT", user, None, "Port", str(port.id), changes)
    db.commit()
    db.refresh(port)
    return port


@router.delete("/{port_id}", status_code=204)
def delete_port(port_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    port = db.get(Port, port_id)
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    record_audit(db, "PORT_DELETED", "PORT", user, None, "Port", str(port.id), {"code": port.code, "name": port.name})
    db.delete(port)
    db.commit()
    return None
