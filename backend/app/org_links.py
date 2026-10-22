"""The Organization Master is the one source of organizations. The roles an organization plays decide which network masters (Airline, Sea Line, IATA, CFS) carry a record for it:
Carrier + Airline -> Airline Master, Carrier + Sea Line -> Sea Line Master, IATA -> IATA Master, CFS -> CFS Master.
Saving the organization creates the linked record (or links the one that was loaded from a file), keeps it in step, and switches it off when the role is removed."""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Airline, CfsRecord, Customer, IataRecord, ShippingLine


def yes(v) -> bool:
    return str(v or "").strip().lower() in ("yes", "true", "1", "y")


def _s(v, n=None):
    v = str(v or "").strip()
    return (v[:n] if n else v) or None


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (v or "").lower())


def _pad_num(v) -> str | None:
    v = str(v or "").strip()
    return v.zfill(3) if v.isdigit() and len(v) <= 3 else (v or None)


def _fill_blank(rec, c: Customer, pairs) -> None:
    """Take the organization's address details for the fields the master record has not got yet."""
    d = c.data or {}
    src = {"address1": d.get("address_line_1"), "address2": d.get("address_line_2"), "city": d.get("city"), "state": d.get("state_province") or d.get("state"), "country": c.country, "email": d.get("official_email") or d.get("email"),
           "phone": d.get("phone") or d.get("mobile")}
    for f in pairs:
        if hasattr(rec, f) and not getattr(rec, f) and _s(src.get(f)):
            setattr(rec, f, _s(src[f], 300))


def _upsert(db: Session, c: Customer, model, role_on: bool, code_key: str, code_label: str, make, update, old_name: str | None, lookup_attr: str):
    """Shared create / link / update / switch off for one network master."""
    existing = db.scalar(select(model).where(model.organization_id == c.organization_id, model.customer_id == c.id))
    if not role_on:
        if existing is not None and existing.status != "INACTIVE":
            existing.status, existing.role_off = "INACTIVE", True
        return existing
    code = _s((c.data or {}).get(code_key), 40)
    if not code:
        raise HTTPException(422, f"{code_label} is required for this role")
    code = code.upper()
    if existing is None:
        found = db.scalar(select(model).where(model.organization_id == c.organization_id, getattr(model, lookup_attr) == code))
        if found is not None and found.customer_id not in (None, c.id):
            other = db.get(Customer, found.customer_id)
            raise HTTPException(409, f"{code_label} {code} already belongs to the organization {other.name if other else ''}. Use a different code, or open that organization")
        if found is not None:          # the record was loaded from a file before the organization existed: link it, never duplicate it
            found.customer_id = c.id
            existing = found
        else:
            existing = make(c, code)
            db.add(existing)
    else:
        clash = db.scalar(select(model.id).where(model.organization_id == c.organization_id, getattr(model, lookup_attr) == code, model.id != existing.id))
        if clash:
            raise HTTPException(409, f"{code_label} {code} is already used by another record")
        setattr(existing, lookup_attr, code)
        if old_name and _norm(existing.name) == _norm(old_name):
            existing.name = c.name[:200]
    update(existing, c)
    if existing.role_off:
        existing.status, existing.role_off = "ACTIVE", False
    return existing


def sync_org(db: Session, c: Customer, old_name: str | None = None) -> None:
    d = c.data or {}
    roles = set(c.roles or [])
    carrier = "carrier" in roles
    want_air, want_sea = carrier and yes(d.get("carrier_airline")), carrier and yes(d.get("carrier_sea_line"))
    # the carrier-type choice is enforced by the Organization form once its role section ships; existing carrier organizations stay savable

    def air_make(c, code):
        return Airline(organization_id=c.organization_id, customer_id=c.id, code=code, name=c.name[:200], num_code=_pad_num(d.get("iata_numeric_code")), icao_code=_s(d.get("icao_code"), 10), status="ACTIVE")

    def air_update(a, c):
        if _s(d.get("iata_numeric_code")):
            a.num_code = _pad_num(d.get("iata_numeric_code"))
        if _s(d.get("icao_code")):
            a.icao_code = _s(d.get("icao_code"), 10).upper()
        _fill_blank(a, c, ("address1", "address2", "city", "state", "country", "email", "phone"))

    _upsert(db, c, Airline, want_air, "airline_code", "Airline Code", air_make, air_update, old_name, "code")

    def sea_make(c, code):
        return ShippingLine(organization_id=c.organization_id, customer_id=c.id, code=code, name=c.name[:200], scac_code=_s(d.get("scac_code"), 10), status="ACTIVE")

    def sea_update(s, c):
        if _s(d.get("scac_code")):
            s.scac_code = _s(d.get("scac_code"), 10).upper()
        _fill_blank(s, c, ("address1", "address2", "city", "state", "country", "email", "phone"))

    _upsert(db, c, ShippingLine, want_sea, "sea_line_code", "Sea Line Code", sea_make, sea_update, old_name, "code")

    def iata_make(c, code):
        return IataRecord(organization_id=c.organization_id, customer_id=c.id, iata_code=code, name=c.name[:200], cass_code=_s(d.get("cass_code"), 20), country=c.country, status="ACTIVE")

    def iata_update(i, c):
        if _s(d.get("cass_code")):
            i.cass_code = _s(d.get("cass_code"), 20)
        for f, v in (("country", c.country), ("city", d.get("city")), ("email", d.get("official_email") or d.get("email")), ("phone", d.get("phone") or d.get("mobile"))):
            if not getattr(i, f) and _s(v):
                setattr(i, f, _s(v, 254))
        if not i.address and _s(d.get("address_line_1")):
            i.address = _s(d.get("address_line_1"), 300)

    _upsert(db, c, IataRecord, "iata" in roles, "iata_agent_code", "IATA Agent Code", iata_make, iata_update, old_name, "iata_code")

    def cfs_make(c, code):
        return CfsRecord(organization_id=c.organization_id, customer_id=c.id, code=code, name=c.name[:200], port_code=_s(d.get("cfs_port_code"), 10), status="ACTIVE")

    def cfs_update(f, c):
        if _s(d.get("cfs_port_code")):
            f.port_code = _s(d.get("cfs_port_code"), 10).upper()
        for fld, v in (("city", d.get("city")), ("state", d.get("state_province") or d.get("state")), ("country", c.country), ("email", d.get("official_email") or d.get("email")), ("phone", d.get("phone") or d.get("mobile"))):
            if not getattr(f, fld) and _s(v):
                setattr(f, fld, _s(v, 254))
        if not f.location and _s(d.get("address_line_1")):
            f.location = _s(d.get("address_line_1"), 200)

    _upsert(db, c, CfsRecord, "cfs" in roles, "cfs_code", "CFS Code", cfs_make, cfs_update, old_name, "code")


def links_of(db: Session, customer_id: int) -> dict:
    """The network master records of an organization (for the organization screens and the vendor list)."""
    out = {}
    for key, model, label in (("airline", Airline, "code"), ("sea_line", ShippingLine, "code"), ("iata", IataRecord, "iata_code"), ("cfs", CfsRecord, "code")):
        r = db.scalar(select(model).where(model.customer_id == customer_id))
        if r is not None:
            out[key] = {"id": r.id, "code": getattr(r, label), "name": r.name, "status": r.status}
    return out


def linked_summary(db: Session, org_id: int) -> dict[int, dict]:
    """customer_id -> links, for a whole company at once."""
    res: dict[int, dict] = {}
    for key, model, label in (("airline", Airline, "code"), ("sea_line", ShippingLine, "code"), ("iata", IataRecord, "iata_code"), ("cfs", CfsRecord, "code")):
        for r in db.scalars(select(model).where(model.organization_id == org_id, model.customer_id.is_not(None))):
            res.setdefault(r.customer_id, {})[key] = {"id": r.id, "code": getattr(r, label), "name": r.name, "status": r.status}
    return res


def blocking_links(db: Session, customer_id: int) -> list[str]:
    from .models import AirlineStockLine

    names = []
    for label, model in (("Airline Master", Airline), ("Sea Line Master", ShippingLine), ("IATA Master", IataRecord), ("CFS Master", CfsRecord), ("Airline Stock Line Master", AirlineStockLine)):
        if db.scalar(select(func.count()).select_from(model).where(model.customer_id == customer_id)):
            names.append(label)
    return names


def attach(db: Session, c: Customer, kind: str, rec) -> None:
    """Link an existing master record to an organization and make the organization show the matching role and details."""
    roles = list(c.roles or [])
    d = dict(c.data or {})
    if kind == "airlines":
        if "carrier" not in roles:
            roles.append("carrier")
        d.update({"carrier_airline": "yes", "airline_code": rec.code, "iata_numeric_code": rec.num_code or d.get("iata_numeric_code", ""), "icao_code": rec.icao_code or d.get("icao_code", "")})
    elif kind == "sea-lines":
        if "carrier" not in roles:
            roles.append("carrier")
        d.update({"carrier_sea_line": "yes", "sea_line_code": rec.code, "scac_code": rec.scac_code or d.get("scac_code", "")})
    elif kind == "iata":
        if "iata" not in roles:
            roles.append("iata")
        d.update({"iata_agent_code": rec.iata_code, "cass_code": rec.cass_code or d.get("cass_code", "")})
    elif kind == "cfs":
        if "cfs" not in roles:
            roles.append("cfs")
        d.update({"cfs_code": rec.code, "cfs_port_code": rec.port_code or d.get("cfs_port_code", "")})
    c.roles, c.data = roles, {k: v for k, v in d.items() if v not in ("", None)}
    if rec.role_off:
        rec.status, rec.role_off = "ACTIVE", False
