"""Reference masters every company starts with (Airline, Sea Line, Charge, HSN / SAC): copied from the owner company's lists, additive only - a company can add its own on top, nothing it already has is touched.
Container Type, Unit, Pack, Incoterm and Country masters are loaded for every company by std_masters / the country kit."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Airline, MasterRecord, Organization, ShippingLine

SKIP = {"id", "organization_id", "customer_id", "source_id", "created_at", "updated_at", "created_by"}
MASTER_TYPES = ("charge", "hsn")


def _clone(db: Session, model, key: str, src: Organization, dst: Organization) -> tuple[int, int]:
    have = {str(getattr(r, key) or "").strip().upper() for r in db.scalars(select(model).where(model.organization_id == dst.id))}
    made = skipped = 0
    for r in db.scalars(select(model).where(model.organization_id == src.id)):
        k = str(getattr(r, key) or "").strip().upper()
        if k in have:
            skipped += 1
            continue
        db.add(model(organization_id=dst.id, **{c.name: getattr(r, c.name) for c in model.__table__.columns if c.name not in SKIP}))
        have.add(k)
        made += 1
    return made, skipped


def copy_reference_masters(db: Session, src: Organization, dst: Organization) -> dict[str, tuple[int, int]]:
    """Copy the reference masters of `src` into `dst`; returns {label: (copied, already there)}. The caller commits."""
    out = {"airlines": _clone(db, Airline, "code", src, dst), "sea lines": _clone(db, ShippingLine, "code", src, dst)}
    for mt in MASTER_TYPES:
        have = {m.code.upper() for m in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == dst.id, MasterRecord.master_type == mt))}
        made = skipped = 0
        for m in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == src.id, MasterRecord.master_type == mt)):
            if m.code.upper() in have:
                skipped += 1
                continue
            db.add(MasterRecord(organization_id=dst.id, master_type=mt, code=m.code, name=m.name, status=m.status, data=dict(m.data or {})))
            have.add(m.code.upper())
            made += 1
        out[mt] = (made, skipped)
    return out


def provision_new_company(db: Session, dst: Organization) -> None:
    """A newly created company starts with the owner company's reference masters (when the owner has any). Never blocks the creation of the company."""
    from .license_mgmt import OWNER_LICENSE

    try:
        src = db.scalar(select(Organization).where(Organization.license_number == OWNER_LICENSE))
        if src is None or src.id == dst.id:
            return
        with db.begin_nested():
            copy_reference_masters(db, src, dst)
    except Exception:       # a failed copy must not stop the company from being created: the masters can be loaded later with scripts_copy_masters.py
        pass
