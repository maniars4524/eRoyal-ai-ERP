from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_current_user
from .db import get_db
from .models import Shipment, User

router = APIRouter(prefix="/api")


@router.get("/shipments")
def list_shipments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.is_super_admin:
        return db.scalars(select(Shipment).order_by(Shipment.created_at.desc())).all()
    return db.scalars(select(Shipment).where(Shipment.organization_id == user.organization_id).order_by(Shipment.created_at.desc())).all()
