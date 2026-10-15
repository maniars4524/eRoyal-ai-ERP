from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, or_, select
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
def list_ports(q: str | None = Query(default=None, max_length=80), port_type: str | None = Query(default=None, max_length=20), country: str | None = Query(default=None, max_length=80),
               status_filter: str | None = Query(default=None, alias="status", pattern="^(ACTIVE|INACTIVE)$"), limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0),
               response: Response = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Searchable, paged list (the master holds ~21,000 ports and airports). The total is returned in the X-Total-Count header."""
    cond = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(Port.code.ilike(like), Port.unlocode.ilike(like), Port.name.ilike(like), Port.city.ilike(like)))
    if port_type:
        cond.append(Port.port_type == port_type)
    if country:
        cond.append(Port.country == country)
    if status_filter:
        cond.append(Port.status == status_filter)
    total = db.scalar(select(func.count()).select_from(Port).where(*cond)) or 0
    # exact code matches first so typing "MAA" or "INMAA" finds that port at the top
    rank = case((Port.code == (q or "").strip().upper(), 0), (Port.code.ilike(f"{(q or '').strip()}%"), 1), else_=2)
    rows = db.scalars(select(Port).where(*cond).order_by(rank, Port.name).limit(limit).offset(offset)).all()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return rows


@router.get("/summary")
def ports_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    types = db.execute(select(Port.port_type, func.count()).group_by(Port.port_type).order_by(Port.port_type)).all()
    countries = db.scalars(select(Port.country).distinct().order_by(Port.country)).all()
    return {"total": sum(c for _, c in types), "types": {t: c for t, c in types}, "countries": countries}


@router.post("/import-global")
def import_global_ports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Load the worldwide sea ports and airports (platform administrators only; existing codes are never overwritten)."""
    _require_super_admin(user)
    from .ports_import import import_global

    out = import_global(db)
    record_audit(db, "PORTS_IMPORTED", "PORT", user, None, "Port", "global", out)
    db.commit()
    return out


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
