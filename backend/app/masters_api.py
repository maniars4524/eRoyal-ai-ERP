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
    "setting": "Company Setting",
}
from . import currency_ref as cr
from .charge_catalogue import CATALOGUE, STANDARD_SAC, validate_activation

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


def _import_standard_sac(db: Session, org_id: int, user: User) -> int:
    made = 0
    for code, name, rate, category in STANDARD_SAC:
        if db.scalar(select(MasterRecord.id).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "hsn", MasterRecord.code == code)):
            continue
        db.add(MasterRecord(organization_id=org_id, master_type="hsn", code=code, name=name, status="ACTIVE", created_by=user.id,
                            data={"source": "standard", "code_type": "SAC (Services)", "gst_rate": rate, "tax_category": "Proposed - verify against the current notification" + ("" if rate else " (rate depends on service and conditions)"), "charge_category": category}))
        made += 1
    return made


@router.post("/hsn/standard/import", status_code=201)
def import_standard_sac(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load the standard freight-forwarding SAC codes (idempotent). Rates are proposals to be verified."""
    org_id = _tenant(user)
    made = _import_standard_sac(db, org_id, user)
    record_audit(db, "HSN_STANDARD_IMPORTED", "MASTER", user, org_id, "hsn", "standard", {"created": made})
    db.commit()
    return {"created": made, "skipped": len(STANDARD_SAC) - made, "note": "Codes and rates are proposals; verify each against the current official notification."}


SAC_FILE = __import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "sac_codes.csv"


@router.post("/hsn/sac-master/import", status_code=201)
def import_sac_master(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load the complete section-wise SAC master (571 codes: section, heading, group, code, description). GST rates are NOT in that file:
    only the codes of the freight starter list get a proposed rate; every other code stays blank until someone sets it. Idempotent."""
    import csv

    org_id = _tenant(user)
    if not SAC_FILE.exists():
        raise HTTPException(status_code=500, detail="The SAC master file is missing on the server")
    proposed = {code: (rate, cat) for code, _n, rate, cat in STANDARD_SAC}
    existing = {r.code: r for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "hsn"))}
    created = enriched = skipped = 0
    with open(SAC_FILE, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            code = row["code"].strip()
            rate, cat = proposed.get(code, ("", ""))
            info = {"code_type": "SAC (Services)", "section": row["section"], "section_name": row["section_name"], "heading": row["heading"], "group": row["group"], "full_description": row["description"][:3900], "source": "sac_master"}
            rec = existing.get(code)
            if rec is None:
                data = {**info, "gst_rate": rate, "charge_category": cat, "tax_category": ("Proposed rate - verify against the current notification" if rate else "Rate not set - confirm from the current notification")}
                db.add(MasterRecord(organization_id=org_id, master_type="hsn", code=code, name=row["description"][:200], status="ACTIVE", created_by=user.id, data=data))
                created += 1
            elif (rec.data or {}).get("source") == "standard":
                rec.data = {**(rec.data or {}), **info}
                rec.name = row["description"][:200]
                enriched += 1
            else:
                skipped += 1
    record_audit(db, "HSN_SAC_MASTER_IMPORTED", "MASTER", user, org_id, "hsn", "sac-master", {"created": created, "enriched": enriched, "skipped": skipped})
    db.commit()
    return {"created": created, "enriched": enriched, "skipped": skipped, "note": "GST rates are not part of the file; set or confirm them from the current official notification."}


@router.post("/charge/catalogue/import", status_code=201)
def import_charge_catalogue(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Create the standard freight charge catalogue (inactive, tax review required). Existing codes are left untouched."""
    org_id = _tenant(user)
    made = skipped = 0
    made_hsn = _import_standard_sac(db, org_id, user)
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


@router.get("/currency/iso-reference")
def currency_iso_reference(user: User = Depends(permission_required("master.read"))):
    """The ISO 4217 table the application validates against (code, name, 3-character numeric code, minor units)."""
    seed = {c: {"symbol": sym, "unicode": uni, "sub": sub, "ratio": ratio, "display_name": name} for c, name, sym, uni, sub, ratio in cr.SEED}
    return [{"code": k, **v, **({"seed": seed[k]} if k in seed else {})} for k, v in sorted(cr.iso_table().items())]


@router.post("/currency/seed", status_code=201)
def seed_currencies(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load the sample currencies (idempotent). Existing currencies are never overwritten; differences from ISO 4217 are reported."""
    org_id = _tenant(user)
    out = cr.seed(db, org_id, user.id)
    record_audit(db, "CURRENCY_SEEDED", "MASTER", user, org_id, "currency", "seed", {"created": len(out["created"]), "skipped": len(out["skipped"]), "conflicts": len(out["conflicts"])})
    db.commit()
    return out


@router.post("/currency/{record_id}/set-base")
def set_base_currency(record_id: int, body: dict, user: User = Depends(permission_required("currency.set_base")), db: Session = Depends(get_db)):
    """The only way to change the organisation's base (home) currency: explicit confirmation, a reason, and the dedicated permission."""
    org_id = _tenant(user)
    if body.get("confirm") is not True or len(str(body.get("reason") or "").strip()) < 5:
        raise HTTPException(422, "Confirm the change and give a reason (at least 5 characters)")
    rec = _get(db, org_id, "currency", record_id)
    previous = cr.set_base(db, org_id, rec)
    record_audit(db, "CURRENCY_BASE_CHANGED", "MASTER", user, org_id, "currency", str(rec.id), {"from": previous.code if previous else None, "to": rec.code, "reason": str(body.get("reason")).strip()})
    db.commit()
    db.refresh(rec)
    return _out(rec)


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
    values = payload.model_dump()
    if master_type == "currency":
        values["data"] = cr.clean(db, org_id, payload.code, payload.name, payload.data, None)
    record = MasterRecord(organization_id=org_id, master_type=master_type, created_by=user.id, **values)
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
    if master_type == "currency":
        if "code" in changes and changes["code"] != record.code and (cr.yes((record.data or {}).get("is_system_currency")) or cr.referenced(db, org_id, record.code)):
            raise HTTPException(409, "The code of a system currency or of a currency that is already in use cannot be changed")
        cr.check_status_change(record, changes.get("status"))
        changes["data"] = cr.clean(db, org_id, changes.get("code") or record.code, changes.get("name") or record.name, changes["data"] if changes.get("data") is not None else (record.data or {}), record)
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
    if master_type == "currency":
        cr.check_delete(db, org_id, record)
    record_audit(db, "MASTER_DELETED", "MASTER", user, org_id, master_type, str(record.id), {"code": record.code, "name": record.name})
    db.delete(record)
    db.commit()
