"""Network masters: Airline, Sea Line (shipping line), Airline Stock Line, IATA and CFS. Every record can be linked to the Organization Master (customer_id) so an organization is never entered twice."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import org_links
from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Airline, AirlineStockLine, CfsRecord, Customer, IataRecord, ShippingLine, User

router = APIRouter(prefix="/api/network", tags=["network masters"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUSES = ("ACTIVE", "INACTIVE")
STATUSES3 = ("ACTIVE", "INACTIVE", "PENDING")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "These masters are tenant-scoped")
    return user.organization_id


# field kinds: (name, type, max length / extra, required, transform)
def F(name, typ="str", n=200, req=False, up=False, rx=None, msg=None):
    return {"name": name, "typ": typ, "n": n, "req": req, "up": up, "rx": rx, "msg": msg}


SPEC: dict[str, dict] = {
    "airlines": {"model": Airline, "label": "Airline", "unique": "code", "title": "name", "fields": [
        F("code", n=10, req=True, up=True, rx=r"^[A-Z0-9]{2,10}$", msg="Airline code must be 2 to 10 letters or digits"), F("name", req=True), F("sub_name"), F("num_code", n=10, rx=r"^\d{1,3}$", msg="IATA number is 1 to 3 digits"),
        F("icao_code", n=10, up=True, rx=r"^[A-Z0-9]{3,4}$", msg="ICAO code is 3 or 4 characters"), F("ac_code", n=20, up=True), F("myc_commission", "dec"), F("country", n=80), F("address1"), F("address2"), F("city", n=80),
        F("state", n=80), F("email", n=254, rx=EMAIL_RE.pattern, msg="Email ID is not valid"), F("phone", n=40), F("status", n=20), F("customer_id", "int")]},
    "sea-lines": {"model": ShippingLine, "label": "Sea Line", "unique": "code", "title": "name", "fields": [
        F("code", n=20, req=True, up=True, rx=r"^[A-Z0-9_\-]{2,20}$", msg="Sea line code must be 2 to 20 letters, digits, - or _"), F("name", req=True), F("sub_name"), F("scac_code", n=10, up=True, rx=r"^[A-Z]{2,4}$", msg="SCAC is 2 to 4 letters"),
        F("country", n=80), F("address1"), F("address2"), F("city", n=80), F("state", n=80), F("email", n=254, rx=EMAIL_RE.pattern, msg="Email ID is not valid"), F("phone", n=40), F("website"), F("status", n=20), F("customer_id", "int")]},
    "iata": {"model": IataRecord, "label": "IATA", "unique": "iata_code", "title": "name", "fields": [
        F("iata_code", n=20, req=True, up=True, rx=r"^[A-Z0-9\-]{3,20}$", msg="IATA code must be 3 to 20 letters or digits"), F("name", req=True), F("airline_id", "int"), F("cass_code", n=20, up=True), F("country", n=80), F("address", n=300),
        F("city", n=80), F("contact_person", n=120), F("phone", n=40), F("email", n=254, rx=EMAIL_RE.pattern, msg="Email ID is not valid"), F("status", n=20), F("customer_id", "int")]},
    "cfs": {"model": CfsRecord, "label": "CFS", "unique": "code", "title": "name", "fields": [
        F("code", n=20, req=True, up=True, rx=r"^[A-Z0-9_\-]{2,20}$", msg="CFS code must be 2 to 20 letters, digits, - or _"), F("name", req=True), F("location"), F("city", n=80), F("state", n=80), F("country", n=80),
        F("port_code", n=10, up=True, rx=r"^[A-Z]{5}$", msg="Port code is a 5-letter UN/LOCODE, e.g. INMAA"), F("contact_person", n=120), F("phone", n=40), F("email", n=254, rx=EMAIL_RE.pattern, msg="Email ID is not valid"), F("status", n=20), F("customer_id", "int")]},
    "stock-lines": {"model": AirlineStockLine, "label": "Airline Stock Line", "unique": None, "title": "from_awb", "fields": [
        F("airline_id", "int", req=True), F("bill_type", n=10, up=True), F("from_awb", n=12, req=True, rx=r"^\d{6,12}$", msg="From AWB must be 6 to 12 digits"), F("to_awb", n=12, req=True, rx=r"^\d{6,12}$", msg="To AWB must be 6 to 12 digits"),
        F("shipper"), F("customer_id", "int"), F("myc_value", "dec"), F("cancel_charge", "dec"), F("received_date", "date"), F("ref_no", n=40), F("remarks", n=400), F("status", n=20)]},
}
STATUS_OF = {"airlines": STATUSES, "sea-lines": STATUSES, "iata": STATUSES3, "cfs": STATUSES3, "stock-lines": STATUSES3}
ALIASES = {
    "airlines": {"airline_code": "code", "airline_name": "name", "airline_sub_name": "sub_name", "iata_number": "num_code", "iata_no": "num_code", "num_code": "num_code", "airline_id": "source_id", "mc": "myc_commission", "myc": "myc_commission",
                 "myc_commission": "myc_commission", "icao": "icao_code", "address_line_1": "address1", "address_line_2": "address2", "email_id": "email"},
    "sea-lines": {"line_code": "code", "sea_line_code": "code", "shipping_line_code": "code", "line_name": "name", "sea_line_name": "name", "shipping_line_name": "name", "scac": "scac_code", "address_line_1": "address1", "address_line_2": "address2", "email_id": "email"},
    "iata": {"iata": "iata_code", "iata_no": "iata_code", "agent_code": "iata_code", "iata_agent_code": "iata_code", "airline_name": "airline", "airline_code": "airline", "email_id": "email", "contact": "contact_person"},
    "cfs": {"cfs_code": "code", "cfs_name": "name", "port": "port_code", "email_id": "email", "contact": "contact_person"},
    "stock-lines": {"awb_no": "from_awb", "from": "from_awb", "to": "to_awb", "type_of_bill": "bill_type", "airline_name": "airline", "airline_code": "airline", "myc": "myc_value", "cancellation_charge": "cancel_charge", "received": "received_date", "ref": "ref_no", "reference": "ref_no"},
}


def _kind(kind: str) -> dict:
    if kind not in SPEC:
        raise HTTPException(404, "Unknown master")
    return SPEC[kind]


def _clean(db: Session, org_id: int, kind: str, data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sp = _kind(kind)
    for f in sp["fields"]:
        n = f["name"]
        if n not in data:
            if f["req"] and not partial:
                raise HTTPException(422, f"{n.replace('_', ' ').capitalize()} is required")
            continue
        v = data[n]
        if f["typ"] == "int":
            v = None if v in (None, "", 0, "0") else int(v)
        elif f["typ"] == "dec":
            try:
                v = Decimal(str(v if v not in (None, "") else 0))
            except InvalidOperation:
                raise HTTPException(422, f"{n.replace('_', ' ').capitalize()} must be a number")
            if v < 0 or (n == "myc_commission" and v > 100):
                raise HTTPException(422, f"{n.replace('_', ' ').capitalize()} is out of range")
        elif f["typ"] == "date":
            if v in (None, ""):
                v = None
            else:
                try:
                    v = date.fromisoformat(str(v)[:10])
                except ValueError:
                    raise HTTPException(422, "Received date must be a valid date")
        else:
            v = (str(v).strip() if v is not None else "")
            if f["up"]:
                v = v.upper()
            if len(v) > f["n"]:
                raise HTTPException(422, f"{n.replace('_', ' ').capitalize()} is too long")
            if v and f["rx"] and not re.match(f["rx"], v):
                raise HTTPException(422, f["msg"] or f"{n} is not valid")
            v = v or None
        if v is None and f["req"]:
            raise HTTPException(422, f"{n.replace('_', ' ').capitalize()} is required")
        out[n] = v
    if "status" in out:
        out["status"] = (out["status"] or "ACTIVE").upper()
        if out["status"] not in STATUS_OF[kind]:
            raise HTTPException(422, "Status must be " + ", ".join(STATUS_OF[kind]))
    if "num_code" in out and out["num_code"]:
        out["num_code"] = out["num_code"].zfill(3)
    if out.get("bill_type") is not None or ("bill_type" in out):
        out["bill_type"] = out.get("bill_type") or "MASTER"
        if out["bill_type"] not in ("MASTER", "HOUSE"):
            raise HTTPException(422, "Type of bill must be MASTER or HOUSE")
    if out.get("customer_id"):
        if not db.scalar(select(Customer.id).where(Customer.id == out["customer_id"], Customer.organization_id == org_id)):
            raise HTTPException(404, "Organization not found")
    if out.get("airline_id"):
        if not db.scalar(select(Airline.id).where(Airline.id == out["airline_id"], Airline.organization_id == org_id)):
            raise HTTPException(404, "Airline not found")
    return out


def _org(c: Customer | None) -> dict | None:
    return {"id": c.id, "code": c.code, "name": c.name, "status": c.status} if c else None


def _out(db: Session, kind: str, r, cust: dict, air: dict) -> dict:
    sp = _kind(kind)
    d = {"id": r.id, "kind": kind, "status": r.status, "customer_id": r.customer_id, "organization": _org(cust.get(r.customer_id)), "updated_at": r.updated_at, "created_at": r.created_at}
    for f in sp["fields"]:
        v = getattr(r, f["name"], None)
        d[f["name"]] = (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, date) else v)
    if hasattr(r, "source_id"):
        d["source_id"] = r.source_id
    if hasattr(r, "role_off"):
        d["role_off"] = r.role_off
    if kind in ("iata", "stock-lines"):
        a = air.get(r.airline_id)
        d["airline_name"], d["airline_code"] = (a.name, a.code) if a else (None, None)
    if kind == "stock-lines":
        d["awb_count"] = int(r.to_awb) - int(r.from_awb) + 1
    return d


def _maps(db: Session, org_id: int) -> tuple[dict, dict]:
    return ({c.id: c for c in db.scalars(select(Customer).where(Customer.organization_id == org_id))}, {a.id: a for a in db.scalars(select(Airline).where(Airline.organization_id == org_id))})


def _check_overlap(db: Session, org_id: int, airline_id: int, f: str, t: str, skip: int | None) -> None:
    lo, hi = int(f), int(t)
    for r in db.scalars(select(AirlineStockLine).where(AirlineStockLine.organization_id == org_id, AirlineStockLine.airline_id == airline_id, AirlineStockLine.status != "INACTIVE")):
        if skip and r.id == skip:
            continue
        if len(r.from_awb) == len(f) and int(r.from_awb) <= hi and lo <= int(r.to_awb):
            raise HTTPException(409, f"This AWB range overlaps the stock line {r.from_awb} to {r.to_awb} of the same airline")


def _validate_stock(db: Session, org_id: int, merged: dict, skip: int | None) -> None:
    f, t = merged.get("from_awb"), merged.get("to_awb")
    if f is None or t is None:
        return
    if len(f) != len(t):
        raise HTTPException(422, "From AWB and To AWB must have the same number of digits")
    if int(f) > int(t):
        raise HTTPException(422, "From AWB cannot be greater than To AWB")
    if merged.get("status") != "INACTIVE":
        _check_overlap(db, org_id, merged["airline_id"], f, t, skip)


def _search(kind: str, q: str):
    m = SPEC[kind]["model"]
    like = f"%{q.strip()}%"
    cols = {"airlines": ("code", "name", "sub_name", "num_code", "icao_code", "country"), "sea-lines": ("code", "name", "sub_name", "scac_code", "country"), "iata": ("iata_code", "name", "cass_code", "country", "contact_person"),
            "cfs": ("code", "name", "location", "city", "country", "contact_person"), "stock-lines": ("from_awb", "to_awb", "shipper", "ref_no")}[kind]
    return or_(*[getattr(m, c).ilike(like) for c in cols])


@router.get("/pickers")
def pickers(user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    """Light lists for the drop-downs of bookings and other screens: active airlines, sea lines, IATA agents, CFS and stock lines."""
    org_id = _tenant(user)
    act = lambda m: select(m).where(m.organization_id == org_id, m.status == "ACTIVE")
    return {"airlines": [{"id": a.id, "code": a.code, "name": a.name, "num_code": a.num_code, "customer_id": a.customer_id} for a in db.scalars(act(Airline).order_by(Airline.name))],
            "sea_lines": [{"id": s.id, "code": s.code, "name": s.name, "scac_code": s.scac_code, "customer_id": s.customer_id} for s in db.scalars(act(ShippingLine).order_by(ShippingLine.name))],
            "iata": [{"id": i.id, "code": i.iata_code, "name": i.name, "airline_id": i.airline_id, "customer_id": i.customer_id} for i in db.scalars(act(IataRecord).order_by(IataRecord.name))],
            "cfs": [{"id": c.id, "code": c.code, "name": c.name, "location": c.location, "city": c.city, "customer_id": c.customer_id} for c in db.scalars(act(CfsRecord).order_by(CfsRecord.name))],
            "stock_lines": [{"id": s.id, "airline_id": s.airline_id, "bill_type": s.bill_type, "from_awb": s.from_awb, "to_awb": s.to_awb, "count": int(s.to_awb) - int(s.from_awb) + 1, "ref_no": s.ref_no} for s in db.scalars(act(AirlineStockLine).order_by(AirlineStockLine.from_awb))]}


@router.get("/{kind}")
def list_records(kind: str, q: str | None = Query(default=None, max_length=100), status_filter: str | None = Query(default=None, alias="status"), country: str | None = None, airline_id: int | None = None, linked: str | None = Query(default=None, pattern="^(yes|no)$"),
                 date_from: date | None = None, date_to: date | None = None, limit: int = Query(default=10, ge=1, le=500), offset: int = Query(default=0, ge=0), sort: str | None = Query(default=None, max_length=30),
                 desc: bool = False, user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    sp = _kind(kind)
    m = sp["model"]
    base = select(m).where(m.organization_id == org_id)
    allq = base
    if q:
        base = base.where(_search(kind, q))
    if status_filter:
        base = base.where(m.status == status_filter.upper())
    if country and hasattr(m, "country"):
        base = base.where(func.lower(m.country) == country.lower())
    if airline_id and hasattr(m, "airline_id"):
        base = base.where(m.airline_id == airline_id)
    if linked and hasattr(m, "customer_id"):
        base = base.where(m.customer_id.is_not(None) if linked == "yes" else m.customer_id.is_(None))
    if date_from and hasattr(m, "received_date"):
        base = base.where(m.received_date >= date_from)
    if date_to and hasattr(m, "received_date"):
        base = base.where(m.received_date <= date_to)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    col = getattr(m, sort, None) if sort and sort in {f["name"] for f in sp["fields"]} | {"updated_at", "created_at", "status"} else None
    col = col if col is not None else getattr(m, sp["title"] if kind != "stock-lines" else "from_awb")
    rows = list(db.scalars(base.order_by(col.desc() if desc else col, m.id).limit(limit).offset(offset)))
    allrows = db.execute(select(m.status, m.updated_at).where(m.organization_id == org_id)).all()
    week = datetime.now(timezone.utc) - timedelta(days=7)
    kpi = {"total": len(allrows), "active": sum(1 for s, _ in allrows if s == "ACTIVE"), "inactive": sum(1 for s, _ in allrows if s == "INACTIVE"), "pending": sum(1 for s, _ in allrows if s == "PENDING"),
           "recent": sum(1 for _, u in allrows if u and u >= week)}
    cust, air = _maps(db, org_id)
    countries = sorted({c for (c,) in db.execute(select(m.country).where(m.organization_id == org_id, m.country.is_not(None))) if c}) if hasattr(m, "country") else []
    return {"items": [_out(db, kind, r, cust, air) for r in rows], "total": total, "kpi": kpi, "countries": countries, "limit": limit, "offset": offset}


@router.post("/{kind}", status_code=201)
def create_record(kind: str, body: dict[str, Any], user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    sp = _kind(kind)
    data = _clean(db, org_id, kind, body, False)
    data.setdefault("status", "ACTIVE")
    if kind == "stock-lines":
        data.setdefault("bill_type", "MASTER")
        _validate_stock(db, org_id, data, None)
    row = sp["model"](organization_id=org_id, created_by=user.id, **data)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A {sp['label']} with this code already exists")
    if row.customer_id and kind != "stock-lines":
        org_links.attach(db, db.get(Customer, row.customer_id), kind, row)
    record_audit(db, "NETWORK_MASTER_CREATED", "MASTER", user, org_id, kind, str(row.id), {k: str(v) for k, v in data.items() if k in ("code", "name", "iata_code", "from_awb", "to_awb")})
    db.commit()
    cust, air = _maps(db, org_id)
    return _out(db, kind, row, cust, air)


def _get(db: Session, org_id: int, kind: str, rid: int):
    r = db.get(_kind(kind)["model"], rid)
    if r is None or r.organization_id != org_id:
        raise HTTPException(404, "Record not found")
    return r


@router.get("/{kind}/export")
def export_records(kind: str, user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    sp = _kind(kind)
    cust, air = _maps(db, org_id)
    names = [f["name"] for f in sp["fields"] if f["name"] != "customer_id"]
    buf = io.StringIO()
    w = csv.writer(buf)
    extra = ["airline_name"] if kind in ("iata", "stock-lines") else []
    w.writerow(names + extra + ["organization", "status_text"] if False else names + extra + ["organization"])
    m = sp["model"]
    for r in db.scalars(select(m).where(m.organization_id == org_id).order_by(m.id)):
        o = _out(db, kind, r, cust, air)
        w.writerow([o.get(n) if o.get(n) is not None else "" for n in names] + [o.get(e) or "" for e in extra] + [(o["organization"] or {}).get("name", "")])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})


@router.get("/{kind}/{rid}")
def get_record(kind: str, rid: int, user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    cust, air = _maps(db, org_id)
    return _out(db, kind, _get(db, org_id, kind, rid), cust, air)


@router.patch("/{kind}/{rid}")
def update_record(kind: str, rid: int, body: dict[str, Any], user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    r = _get(db, org_id, kind, rid)
    data = _clean(db, org_id, kind, body, True)
    if kind == "stock-lines":
        merged = {"airline_id": r.airline_id, "from_awb": r.from_awb, "to_awb": r.to_awb, "status": r.status, **data}
        _validate_stock(db, org_id, merged, r.id)
    for k, v in data.items():
        setattr(r, k, v)
    if "status" in data and hasattr(r, "role_off"):
        r.role_off = False
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Another record already uses this code")
    if "customer_id" in data and r.customer_id and kind != "stock-lines":
        org_links.attach(db, db.get(Customer, r.customer_id), kind, r)
    record_audit(db, "NETWORK_MASTER_UPDATED", "MASTER", user, org_id, kind, str(r.id), {k: str(v) for k, v in data.items()})
    db.commit()
    cust, air = _maps(db, org_id)
    return _out(db, kind, r, cust, air)


@router.delete("/{kind}/{rid}", status_code=204)
def delete_record(kind: str, rid: int, user: User = Depends(permission_required("master.delete")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    r = _get(db, org_id, kind, rid)
    if kind == "airlines":
        used = (db.scalar(select(func.count()).select_from(AirlineStockLine).where(AirlineStockLine.airline_id == r.id)) or 0) + (db.scalar(select(func.count()).select_from(IataRecord).where(IataRecord.airline_id == r.id)) or 0)
        if used:
            raise HTTPException(409, "This airline is used by stock lines or IATA records. Deactivate it instead")
    if getattr(r, "customer_id", None) and kind != "stock-lines":
        raise HTTPException(409, "This record is linked to an organization. Unlink it first, or remove the role in the Organization Master")
    record_audit(db, "NETWORK_MASTER_DELETED", "MASTER", user, org_id, kind, str(r.id), {})
    db.delete(r)
    db.commit()


class LinkIn(BaseModel):
    customer_id: int | None = None
    create: bool = False


@router.post("/{kind}/{rid}/link")
def link_organization(kind: str, rid: int, body: LinkIn, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    """Link the record to an organization of the Organization Master (or create the organization from it); customer_id null unlinks."""
    org_id = _tenant(user)
    if kind == "stock-lines":
        raise HTTPException(422, "A stock line is linked to its airline")
    r = _get(db, org_id, kind, rid)
    if body.create:
        base = re.sub(r"[^A-Z0-9]", "", ((getattr(r, "code", None) or getattr(r, "iata_code", None) or "X")).upper())
        prefix = {"airlines": "AIR", "sea-lines": "SEA", "iata": "IATA", "cfs": "CFS"}[kind]
        code, n = f"{prefix}-{base}"[:20], 1
        while db.scalar(select(Customer.id).where(Customer.organization_id == org_id, Customer.code == code)):
            n += 1
            code = f"{prefix}-{base}"[:16] + f"-{n}"
        from . import coa

        name = r.name
        if db.scalar(select(Customer.id).where(Customer.organization_id == org_id, func.lower(Customer.name) == name.lower())):
            raise HTTPException(409, f"An organization named {name} already exists. Link to it instead")
        c = Customer(organization_id=org_id, created_by=user.id, code=code, name=name[:160], category="Carrier" if kind in ("airlines", "sea-lines") else "Business", country=(getattr(r, "country", None) or "India"), roles=["carrier"] if kind in ("airlines", "sea-lines") else ["iata" if kind == "iata" else "cfs"], data={})
        db.add(c)
        db.flush()
        r.customer_id = c.id
        org_links.attach(db, c, kind, r)
        coa.sync_customer(db, c, user.id)
    elif body.customer_id:
        c = db.scalar(select(Customer).where(Customer.id == body.customer_id, Customer.organization_id == org_id))
        if c is None:
            raise HTTPException(404, "Organization not found")
        other = db.scalar(select(type(r).id).where(type(r).customer_id == c.id, type(r).id != r.id))
        if other:
            raise HTTPException(409, f"{c.name} is already linked to another {SPEC[kind]['label']} record")
        r.customer_id = c.id
        org_links.attach(db, c, kind, r)
        from . import coa

        coa.sync_customer(db, c, user.id)
    else:
        r.customer_id = None
    record_audit(db, "NETWORK_MASTER_LINKED", "MASTER", user, org_id, kind, str(r.id), {"customer_id": r.customer_id})
    db.commit()
    cust, air = _maps(db, org_id)
    return _out(db, kind, r, cust, air)


# ---------------------------------------------------------------- import
class ImportIn(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=5000)
    apply: bool = False


def _normkey(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")


def _import(db: Session, org_id: int, user: User, kind: str, rows: list[dict], apply: bool) -> dict:
    sp = _kind(kind)
    m = sp["model"]
    alias = ALIASES.get(kind, {})
    existing_codes = {getattr(r, sp["unique"]).upper() for r in db.scalars(select(m).where(m.organization_id == org_id))} if sp["unique"] else set()
    custs = {re.sub(r"[^a-z0-9]", "", c.name.lower()): c for c in db.scalars(select(Customer).where(Customer.organization_id == org_id))}
    taken_customers = {r.customer_id for r in db.scalars(select(m).where(m.organization_id == org_id)) if getattr(r, "customer_id", None)}
    airlines = {a.code.upper(): a for a in db.scalars(select(Airline).where(Airline.organization_id == org_id))} if kind in ("iata", "stock-lines") else {}
    airlines_by_name = {a.name.upper(): a for a in airlines.values()}
    norm = []
    for raw in rows:
        r = {}
        for k, v in raw.items():
            nk = _normkey(k)
            r[alias.get(nk, nk)] = v
        norm.append(r)
    # rows that only repeat another row of the file under a number used as a code
    by_name_num = {}
    for r in norm:
        if kind == "airlines":
            by_name_num.setdefault((str(r.get("name", "")).strip().upper(), str(r.get("num_code", "")).strip()), []).append(r)
    out, adds, padded = [], [], 0
    seen: set[str] = set()
    for i, r in enumerate(norm, start=1):
        label = str(r.get(sp["unique"] or "from_awb", "") or r.get("name", "")).strip()
        def res(status, msg=""):
            out.append({"row": i, "code": str(r.get("code") or r.get("iata_code") or r.get("from_awb") or "").strip(), "name": str(r.get("name") or r.get("airline") or r.get("shipper") or "").strip(), "status": status, "message": msg})
        if kind == "airlines":
            name = str(r.get("name", "")).strip()
            code = str(r.get("code", "")).strip().upper()
            if re.match(r"^air\s*line\s*name$", name, re.I):
                res("SKIP", "Placeholder row (the name is the column title)")
                continue
            twin = [x for x in by_name_num.get((name.upper(), str(r.get("num_code", "")).strip()), []) if x is not r and re.match(r"^[A-Za-z][A-Za-z0-9]{1,2}$", str(x.get("code", "")).strip())]
            if code.isdigit() and twin:
                res("SKIP", f"The code {code} is the numeric prefix; the airline is already in the list as {str(twin[0].get('code')).strip().upper()}")
                continue
        try:
            data = {k: v for k, v in r.items() if k in {f["name"] for f in sp["fields"]}}
            if kind == "airlines":
                nc = str(data.get("num_code") or "").strip()
                if nc and not nc.isdigit():
                    data.pop("num_code")
                elif nc and len(nc) < 3:
                    padded += 1
            if kind in ("iata", "stock-lines"):
                ref = str(r.get("airline", "") or "").strip().upper()
                a = airlines.get(ref) or airlines_by_name.get(ref)
                if kind == "stock-lines" and a is None:
                    raise HTTPException(422, "Airline not found in the Airline Master" if ref else "Airline is required")
                if a is not None:
                    data["airline_id"] = a.id
            data.pop("customer_id", None)
            data.pop("status", None) if str(data.get("status", "")).upper() not in STATUS_OF[kind] else None
            clean = _clean(db, org_id, kind, data, False)
            if kind == "stock-lines":
                clean.setdefault("bill_type", "MASTER")
                _validate_stock(db, org_id, {**clean, "status": clean.get("status", "ACTIVE")}, None)
        except HTTPException as e:
            res("ERROR", e.detail)
            continue
        uniq = (clean.get(sp["unique"]) or "").upper() if sp["unique"] else None
        if uniq and (uniq in existing_codes or uniq in seen):
            res("SKIP", "Already in the master (same code)")
            continue
        if uniq:
            seen.add(uniq)
        if kind == "stock-lines":
            key = f"{clean['airline_id']}:{clean['from_awb']}"
            for prev in adds:
                if prev[1].get("airline_id") == clean["airline_id"] and prev[1]["status"] != "INACTIVE" and int(prev[1]["from_awb"]) <= int(clean["to_awb"]) and int(clean["from_awb"]) <= int(prev[1]["to_awb"]):
                    res("ERROR", "Overlaps another range of the same airline in this file")
                    break
            else:
                adds.append((i, clean, None))
                res("NEW")
            continue
        match = None
        if kind != "stock-lines" and "customer_id" in {f["name"] for f in sp["fields"]}:
            c = custs.get(re.sub(r"[^a-z0-9]", "", str(clean.get("name", "")).lower()))
            if c is not None and c.id not in taken_customers:
                match = c
                taken_customers.add(c.id)
        adds.append((i, clean, match))
        res("NEW", ("Linked to the organization " + match.name) if match else "")
    result = {"rows": out, "new": len(adds), "skipped": sum(1 for x in out if x["status"] == "SKIP"), "errors": sum(1 for x in out if x["status"] == "ERROR"), "padded": padded, "linked": sum(1 for a in adds if a[2]), "applied": False}
    if apply:
        if result["errors"]:
            raise HTTPException(422, f"{result['errors']} row(s) have errors. Correct them or remove them and try again")
        for i, clean, match in adds:
            src = norm[i - 1].get("source_id")
            row = m(organization_id=org_id, created_by=user.id, **clean)
            if hasattr(row, "source_id") and str(src or "").isdigit():
                row.source_id = int(src)
            if match is not None:
                row.customer_id = match.id
            row.status = clean.get("status", "ACTIVE")
            db.add(row)
            db.flush()
            if match is not None:
                org_links.attach(db, match, kind, row)
        record_audit(db, "NETWORK_MASTER_IMPORTED", "MASTER", user, org_id, kind, "bulk", {"new": len(adds), "skipped": result["skipped"]})
        db.commit()
        result["applied"] = True
    return result


@router.post("/{kind}/import")
def import_rows(kind: str, body: ImportIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load records from a pasted / uploaded CSV (the first row names the columns). apply=false only checks the rows. Records that already exist are skipped, never duplicated."""
    return _import(db, _tenant(user), user, kind, body.rows, body.apply)


class StdIn(BaseModel):
    apply: bool = False


@router.post("/airlines/import-standard")
def import_standard_airlines(body: StdIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load the company's Airline master file (airline id, code, name, sub name, IATA number). Placeholder and repeated rows are skipped and reported; airlines that already exist are never duplicated."""
    path = Path(__file__).resolve().parent.parent / "data" / "airlines.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return _import(db, _tenant(user), user, "airlines", rows, body.apply)
