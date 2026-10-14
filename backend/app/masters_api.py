import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import MasterRecord, User

router = APIRouter(prefix="/api/masters", tags=["masters"])

# Master types managed by this generic framework. Add a type here to expose it.
MASTER_TYPES = {
    "country": "Country",
    "currency": "Currency",
    "unit": "Unit of Measurement",
    "pack": "Package Type",
    "incoterm": "Incoterm",
    "hsn": "HSN / SAC Code",
    "container_type": "Container Type",
    "charge": "Charge",
    "terms": "Terms & Conditions",
    "department": "Department",
    "designation": "Designation",
    "branch": "Branch",
    "employee": "Employee",
    "route": "Route",
    "account": "Ledger Account",
}
from .charge_catalogue import CATALOGUE, HEADINGS, validate_activation

KEY_RE = re.compile(r"^[a-z0-9_]{1,60}$")
CODE_RE = re.compile(r"^[A-Za-z0-9._/\- ]{1,40}$")
MAX_FIELDS = 200
MAX_TEXT = 4000


def _clean_code(value: str) -> str:
    value = value.strip().upper()
    if not CODE_RE.match(value):
        raise ValueError("code may only contain letters, digits and . _ / - space")
    return value


def _clean_data(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > MAX_FIELDS:
        raise ValueError("too many fields")
    for key, item in value.items():
        if not KEY_RE.match(key):
            raise ValueError(f"invalid field name: {key[:30]}")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"field {key} must be a scalar value")
        if isinstance(item, str) and len(item) > MAX_TEXT:
            raise ValueError(f"field {key} is too long")
    return value


class MasterPayload(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _clean_code(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("data")
    @classmethod
    def _data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_data(value)


class MasterPatch(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    data: dict[str, Any] | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, value: str | None) -> str | None:
        return None if value is None else _clean_code(value)

    @field_validator("data")
    @classmethod
    def _data(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _clean_data(value)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Master data is tenant-scoped; Super Admin does not manage it directly")
    return user.organization_id


def _type(master_type: str) -> str:
    if master_type not in MASTER_TYPES:
        raise HTTPException(status_code=404, detail="Unknown master type")
    return master_type


def _out(r: MasterRecord) -> dict:
    return {"id": r.id, "master_type": r.master_type, "code": r.code, "name": r.name, "status": r.status, "data": r.data or {}, "created_at": r.created_at, "updated_at": r.updated_at}


def _get(db: Session, org_id: int, master_type: str, record_id: int) -> MasterRecord:
    record = db.scalar(select(MasterRecord).where(MasterRecord.id == record_id, MasterRecord.organization_id == org_id, MasterRecord.master_type == master_type))
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


def _check_charge_activation(master_type: str, status_value: str, data: dict | None) -> None:
    if master_type == "charge" and status_value == "ACTIVE":
        try:
            validate_activation(data or {})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))


@router.post("/charge/catalogue/import", status_code=201)
def import_charge_catalogue(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Create the standard freight charge catalogue (inactive, tax review required). Existing codes are left untouched."""
    org_id = _tenant(user)
    made_hsn = made = skipped = 0
    for code, label in HEADINGS.items():
        if not db.scalar(select(MasterRecord.id).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "hsn", MasterRecord.code == code)):
            db.add(MasterRecord(organization_id=org_id, master_type="hsn", code=code, name=label, status="ACTIVE", created_by=user.id, data={"code_type": "SAC (Services)", "tax_category": "Heading only - rate depends on the service and conditions"}))
            made_hsn += 1
    for code, name, group, sac, note, pct, config in CATALOGUE:
        if db.scalar(select(MasterRecord.id).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "charge", MasterRecord.code == code)):
            skipped += 1
            continue
        data = {"catalogue": "yes", "tax_status": "REVIEW_REQUIRED", "hsn_code": sac, "gst_percent": "" if pct is None else str(pct), "category": group, "classification": note, "tax_note": config,
                "tax_treatment": "taxable", "ar_ap": "Both", "uom": "Flat", "module": "ALL", "mode": "ALL"}
        db.add(MasterRecord(organization_id=org_id, master_type="charge", code=code, name=name, status="INACTIVE", created_by=user.id, data=data))
        made += 1
    record_audit(db, "CHARGE_CATALOGUE_IMPORTED", "MASTER", user, org_id, "charge", "catalogue", {"charges": made, "skipped": skipped, "headings": made_hsn})
    db.commit()
    return {"charges_created": made, "charges_skipped": skipped, "headings_created": made_hsn, "note": "Charges are inactive until the SAC, rate, notification reference and effective date are verified and recorded."}


@router.get("/types")
def list_types(user: User = Depends(permission_required("master.read"))):
    return [{"type": key, "label": label} for key, label in MASTER_TYPES.items()]


@router.get("/{master_type}")
def list_records(
    master_type: str,
    q: str | None = Query(default=None, max_length=100),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(ACTIVE|INACTIVE)$"),
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(permission_required("master.read")),
    db: Session = Depends(get_db),
):
    org_id, master_type = _tenant(user), _type(master_type)
    query = select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == master_type)
    if status_filter:
        query = query.where(MasterRecord.status == status_filter)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(MasterRecord.code.ilike(like), MasterRecord.name.ilike(like)))
    return [_out(r) for r in db.scalars(query.order_by(MasterRecord.code).limit(limit).offset(offset))]


@router.post("/{master_type}", status_code=201)
def create_record(master_type: str, payload: MasterPayload, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    org_id, master_type = _tenant(user), _type(master_type)
    _check_charge_activation(master_type, payload.status, payload.data)
    record = MasterRecord(organization_id=org_id, master_type=master_type, created_by=user.id, **payload.model_dump())
    db.add(record)
    try:
        db.flush()
        record_audit(db, "MASTER_CREATED", "MASTER", user, org_id, master_type, str(record.id), {"code": record.code, "name": record.name})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{MASTER_TYPES[master_type]} code already exists")
    db.refresh(record)
    return _out(record)


@router.get("/{master_type}/{record_id}")
def get_record(master_type: str, record_id: int, user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    return _out(_get(db, _tenant(user), _type(master_type), record_id))


@router.patch("/{master_type}/{record_id}")
def update_record(master_type: str, record_id: int, payload: MasterPatch, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    org_id, master_type = _tenant(user), _type(master_type)
    record = _get(db, org_id, master_type, record_id)
    changes = payload.model_dump(exclude_unset=True)
    was_active = record.status == "ACTIVE"
    _check_charge_activation(master_type, changes.get("status") or record.status, changes.get("data") if changes.get("data") is not None else record.data)
    for key, value in changes.items():
        if value is not None:
            setattr(record, key, value)
    try:
        if master_type == "charge" and record.status == "ACTIVE" and not was_active and (record.data or {}).get("catalogue") == "yes":
            record_audit(db, "CHARGE_TAX_ACTIVATED", "MASTER", user, org_id, master_type, str(record.id), {"code": record.code, "gst_percent": (record.data or {}).get("gst_percent"), "hsn_code": (record.data or {}).get("hsn_code"),
                                                                                                           "notification_ref": (record.data or {}).get("notification_ref"), "effective_date": (record.data or {}).get("effective_date")})
        record_audit(db, "MASTER_UPDATED", "MASTER", user, org_id, master_type, str(record.id), {k: v for k, v in changes.items() if k != "data"})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{MASTER_TYPES[master_type]} code already exists")
    db.refresh(record)
    return _out(record)


@router.delete("/{master_type}/{record_id}", status_code=204)
def delete_record(master_type: str, record_id: int, user: User = Depends(permission_required("master.delete")), db: Session = Depends(get_db)):
    org_id, master_type = _tenant(user), _type(master_type)
    record = _get(db, org_id, master_type, record_id)
    record_audit(db, "MASTER_DELETED", "MASTER", user, org_id, master_type, str(record.id), {"code": record.code, "name": record.name})
    db.delete(record)
    db.commit()
