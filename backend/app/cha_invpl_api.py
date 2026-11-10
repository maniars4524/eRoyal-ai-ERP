"""CHA > Invoice & Packing List: the CHA prepares the commercial invoice and the packing list on behalf of the shipper.

The record keeps the header, the parties, the route and the item lines; the printed documents (invoice, packing list, both, invoice-cum-packing list) are built from it on the screen.
"Get Bill" fills a new record from a job (parties, route, flight, cargo) so nothing is typed twice.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import ChaInvoicePacking, JobDetail, Shipment, User

router = APIRouter(prefix="/api/cha/invoice-packing", tags=["cha invoice packing"])


class InvPlIn(BaseModel):
    invoice_no: str = Field(default="", max_length=60)
    auto_number: bool = False          # the server issues the next running number (CI/26-27/0001) when the record is new
    invoice_date: date | None = None
    job_number: str | None = Field(default=None, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Invoice & Packing List is tenant-scoped")
    return user.organization_id


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v).replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


def _fy(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y % 100:02d}-{(y + 1) % 100:02d}"


def _next_number(db: Session, org_id: int, on: date | None = None) -> str:
    """CI/<financial year>/<running number>, taken from the highest number already issued in that year (gap-free enough for a draft number; the caller holds a lock)."""
    pref = f"CI/{_fy(on or date.today())}/"
    last = 0
    for (no,) in db.execute(select(ChaInvoicePacking.invoice_no).where(ChaInvoicePacking.organization_id == org_id, ChaInvoicePacking.invoice_no.like(pref + "%"))):
        try:
            last = max(last, int(no[len(pref):]))
        except ValueError:
            pass
    return f"{pref}{last + 1:04d}"


def _clean(body: InvPlIn) -> dict:
    d = dict(body.data or {})
    if len(str(d)) > 250_000:
        raise HTTPException(422, "Too much data in this record")
    lines = d.get("lines") or []
    if not isinstance(lines, list) or len(lines) > 200:
        raise HTTPException(422, "At most 200 item lines")
    for i, ln in enumerate(lines, start=1):
        if not isinstance(ln, dict):
            raise HTTPException(422, f"Item line {i} is not valid")
        for k in ("pkgs", "qty", "net_wt", "gross_wt", "rate", "amount"):
            if ln.get(k) not in (None, "") and _dec(ln.get(k)) < 0:
                raise HTTPException(422, f"Item line {i}: {k.replace('_', ' ')} cannot be negative")
    d["direction"] = "Import" if str(d.get("direction") or "").strip().lower() == "import" else "Export"
    d["lines"] = lines
    cur = str(d.get("currency") or "USD").upper()[:3]
    d["currency"] = cur
    return d


def _total(d: dict) -> Decimal:
    t = Decimal(0)
    for ln in d.get("lines") or []:
        amt = ln.get("amount")
        t += _dec(amt) if amt not in (None, "") else _dec(ln.get("qty")) * _dec(ln.get("rate"))
    return t.quantize(Decimal("0.01"))


def _out(r: ChaInvoicePacking, full: bool = False) -> dict:
    out = {"id": r.id, "invoice_no": r.invoice_no, "invoice_date": r.invoice_date, "job_number": r.job_number, "exporter": r.exporter, "consignee": r.consignee, "currency": r.currency,
           "total": str(r.total or 0), "updated_at": r.updated_at, "direction": (r.data or {}).get("direction") or "Export"}
    if full:
        out["data"] = r.data or {}
    return out


def _get(db: Session, org_id: int, rid: int) -> ChaInvoicePacking:
    r = db.scalar(select(ChaInvoicePacking).where(ChaInvoicePacking.id == rid, ChaInvoicePacking.organization_id == org_id))
    if not r:
        raise HTTPException(404, "Invoice & Packing List not found")
    return r


@router.get("")
def list_records(q: str = "", limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    stmt = select(ChaInvoicePacking).where(ChaInvoicePacking.organization_id == org_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ChaInvoicePacking.invoice_no.ilike(like), ChaInvoicePacking.exporter.ilike(like), ChaInvoicePacking.consignee.ilike(like), ChaInvoicePacking.job_number.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ChaInvoicePacking.id.desc()).offset(offset).limit(limit)).all()
    return {"total": total, "items": [_out(r) for r in rows]}


@router.get("/next-number")
def next_number(user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """The number the next automatic Invoice & Packing List will get."""
    return {"invoice_no": _next_number(db, _tenant(user))}


@router.get("/from-job/{job_number:path}")
def from_job(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """"Get Bill": the parties, route, flight and cargo of a job, as the starting point of a new invoice and packing list."""
    org_id = _tenant(user)
    job = db.scalar(select(Shipment).where(Shipment.organization_id == org_id, Shipment.job_number == job_number))
    if not job:
        raise HTTPException(404, "Job not found")
    det = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    p = (det.parties if det else None) or {}
    c = (det.cargo if det else None) or {}
    from .jobs_lifecycle_api import party_text

    txt = lambda k: party_text(db, org_id, p.get(k), None, p.get(k + "Branch")) if p.get(k) else ""           # name and address, with the branch chosen on the job
    data = {"exporter": txt("shipper"), "consignee": txt("consignee"), "notify": txt("notify1"), "port_of_loading": job.pol or "", "port_of_discharge": job.pod or "",
            "final_destination": job.pod or "", "pre_carriage": "AIR" if job.mode == "Air" else "SEA" if job.mode == "Sea" else (job.mode or ""), "lines": []}
    if c.get("cargoDesc") or c.get("pkgs") or c.get("gwt"):
        data["lines"] = [{"pkgs": c.get("pkgs") or "", "pkg_unit": c.get("pkgType") or "PKG", "description": c.get("cargoDesc") or "", "net_wt": c.get("nwt") or "", "gross_wt": c.get("gwt") or ""}]
    return {"job_number": job.job_number, "data": data}


@router.get("/{rid}")
def get_record(rid: int, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    return _out(_get(db, _tenant(user), rid), True)


@router.post("", status_code=201)
def create_record(body: InvPlIn, user: User = Depends(permission_required("shipment.create")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    d = _clean(body)
    no = body.invoice_no.strip()
    if body.auto_number or not no:
        db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": 6810000 + org_id})            # two people saving at once never get the same number
        no = _next_number(db, org_id, body.invoice_date)
    r = ChaInvoicePacking(organization_id=org_id, invoice_no=no, invoice_date=body.invoice_date, job_number=(body.job_number or "").strip() or None, exporter=str(d.get("exporter") or "").split("\n")[0][:200],
                          consignee=str(d.get("consignee") or "").split("\n")[0][:200], currency=d["currency"], total=_total(d), data=d, created_by=user.id)
    db.add(r)
    db.flush()
    record_audit(db, "CHA_INVPL_CREATED", "CHA", user, org_id, "ChaInvoicePacking", str(r.id), {"invoice_no": r.invoice_no, "job_number": r.job_number})
    db.commit()
    db.refresh(r)
    return _out(r, True)


@router.put("/{rid}")
def update_record(rid: int, body: InvPlIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    r = _get(db, org_id, rid)
    d = _clean(body)
    if not body.invoice_no.strip():
        raise HTTPException(422, "Invoice number is required")
    r.invoice_no, r.invoice_date, r.job_number = body.invoice_no.strip(), body.invoice_date, (body.job_number or "").strip() or None
    r.exporter, r.consignee = str(d.get("exporter") or "").split("\n")[0][:200], str(d.get("consignee") or "").split("\n")[0][:200]
    r.currency, r.total, r.data = d["currency"], _total(d), d
    record_audit(db, "CHA_INVPL_UPDATED", "CHA", user, org_id, "ChaInvoicePacking", str(r.id), {"invoice_no": r.invoice_no})
    db.commit()
    db.refresh(r)
    return _out(r, True)


@router.delete("/{rid}", status_code=204)
def delete_record(rid: int, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    org_id = _tenant(user)
    r = _get(db, org_id, rid)
    record_audit(db, "CHA_INVPL_DELETED", "CHA", user, org_id, "ChaInvoicePacking", str(r.id), {"invoice_no": r.invoice_no})
    db.delete(r)
    db.commit()
