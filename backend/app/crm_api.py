"""CRM records API: leads, enquiries, opportunities, follow-ups and CRM bookings, stored per company.

Writes are optimistic: a client sends the `version` it loaded (0 for a new record) and gets 409 if someone else changed it first.
Reads need quote.read, writes need quote.create (the sales team's permissions).
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import permission_required
from .db import get_db
from .models import CrmRecord, Customer, User

router = APIRouter(prefix="/api/crm", tags=["crm"])
KINDS = ("lead", "enquiry", "opportunity", "followup", "booking")


class CrmIn(BaseModel):
    ref: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._/-]+$")
    version: int = Field(default=0, ge=0)
    customer_id: int | None = None
    company: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown CRM record type")
    return kind


def _out(r: CrmRecord) -> dict:
    return {"id": r.id, "kind": r.kind, "ref": r.ref, "customer_id": r.customer_id, "company": r.company, "status": r.status, "data": r.data or {}, "version": r.version,
            "created_at": r.created_at.isoformat() if r.created_at else None, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/{kind}")
def list_records(kind: str, user: User = Depends(permission_required("quote.read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(CrmRecord).where(CrmRecord.organization_id == user.organization_id, CrmRecord.kind == _kind(kind)).order_by(CrmRecord.id.desc())).all()
    return [_out(r) for r in rows]


@router.put("/{kind}")
def save_record(kind: str, body: CrmIn, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    kind = _kind(kind)
    customer = None
    if body.customer_id is not None:
        customer = db.scalar(select(Customer).where(Customer.id == body.customer_id, Customer.organization_id == user.organization_id))
        if not customer:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Customer not found in the Customer Master")
    row = db.scalar(select(CrmRecord).where(CrmRecord.organization_id == user.organization_id, CrmRecord.kind == kind, CrmRecord.ref == body.ref).with_for_update())
    company = customer.name if customer else body.company
    if row is None:
        if body.version != 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "This record no longer exists. Refresh and try again.")
        row = CrmRecord(organization_id=user.organization_id, kind=kind, ref=body.ref, created_by=user.id, version=1)
        db.add(row)
    else:
        if row.version != body.version:
            raise HTTPException(status.HTTP_409_CONFLICT, "This record was changed by someone else. Refresh and try again.")
        row.version += 1
    row.customer_id, row.company, row.status, row.data = (customer.id if customer else None), company, body.status, body.data
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A record with this number already exists. Refresh and try again.")
    db.refresh(row)
    return _out(row)


@router.delete("/{kind}/{ref}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(kind: str, ref: str, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    row = db.scalar(select(CrmRecord).where(CrmRecord.organization_id == user.organization_id, CrmRecord.kind == _kind(kind), CrmRecord.ref == ref))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
