"""Settings → Sequence Setting API. All rules (length, scope conflicts, permissions) are enforced here and in app/sequences.py; the UI only reflects them."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import sequences as sq
from .auth import get_current_user, has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import IssuedNumber, SequenceConfig, SequenceCounter, SequenceEvent, User

router = APIRouter(prefix="/api/sequences", tags=["sequence-setting"])


def _org(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Sequence Setting is company-scoped")
    return user.organization_id


class Comp(BaseModel):
    k: str
    v: str | None = Field(default=None, max_length=12)


class ConfigIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    doc_type: str
    branch_scope: str = Field(default="ALL", max_length=20)
    fy_scope: str = Field(default="ALL", max_length=12)
    shipment_scope: str = Field(default="ALL", max_length=40)
    components: list[Comp] = Field(default_factory=list)
    number_width: int = Field(default=5, ge=1, le=12)
    start_number: int = Field(default=1, ge=0)
    step: int = Field(default=1, ge=1, le=1000)
    reset_policy: str = Field(default="FY", pattern="^(NEVER|FY|CALENDAR)$")
    max_length: int | None = Field(default=None, ge=4, le=60)
    doc_code: str | None = Field(default=None, max_length=12)
    fy_format: str = Field(default="YY-YY", max_length=10)
    shipment_codes: dict[str, str] = Field(default_factory=dict)
    effective_from: date | None = None
    effective_to: date | None = None
    remarks: str | None = Field(default=None, max_length=500)


class ConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    branch_scope: str | None = Field(default=None, max_length=20)
    fy_scope: str | None = Field(default=None, max_length=12)
    shipment_scope: str | None = Field(default=None, max_length=40)
    components: list[Comp] | None = None
    number_width: int | None = Field(default=None, ge=1, le=12)
    start_number: int | None = Field(default=None, ge=0)
    step: int | None = Field(default=None, ge=1, le=1000)
    reset_policy: str | None = Field(default=None, pattern="^(NEVER|FY|CALENDAR)$")
    max_length: int | None = Field(default=None, ge=4, le=60)
    doc_code: str | None = Field(default=None, max_length=12)
    fy_format: str | None = Field(default=None, max_length=10)
    shipment_codes: dict[str, str] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remarks: str | None = Field(default=None, max_length=500)


class Reason(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class AdjustIn(BaseModel):
    next_number: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=1000)
    period: str | None = Field(default=None, max_length=12)


class SuggestIn(BaseModel):
    doc_type: str


class IssueIn(BaseModel):
    doc_type: str
    branch: str | None = Field(default=None, max_length=20)


def _fin_guard(user: User, doc_type: str) -> None:
    if sq.info(doc_type)["financial"] and not has_permission(user, "sequence.configure_financial"):
        raise HTTPException(403, "Invoice and voucher sequences need the 'Configure invoice / voucher sequences' permission")


def _norm(data: dict) -> dict:
    d = dict(data)
    if "components" in d and d["components"] is not None:
        d["components"] = [{"k": c["k"], **({"v": c["v"]} if c.get("v") not in (None, "") else {})} for c in d["components"]]
    for k in ("doc_code",):
        if d.get(k):
            d[k] = d[k].strip().upper()
    if d.get("shipment_codes") is not None:
        d["shipment_codes"] = {k: v.strip().upper() for k, v in d["shipment_codes"].items() if v and v.strip()}
    if d.get("branch_scope"):
        d["branch_scope"] = d["branch_scope"].strip().upper()
    for k in ("prefix",):
        pass
    for comp in d.get("components") or []:
        if comp.get("k") in ("prefix", "suffix", "custom") and comp.get("v"):
            comp["v"] = comp["v"].strip().upper()
    return d


def _out(db: Session, c: SequenceConfig, with_next: bool = True) -> dict:
    i = sq.info(c.doc_type)
    nxt = sq.next_preview(db, c) if with_next else None
    return {"id": c.id, "name": c.name, "module": c.module, "doc_type": c.doc_type, "doc_label": i["label"], "financial": i["financial"], "integrated": i["integrated"], "shipment_aware": i["shipment_aware"],
            "branch_scope": c.branch_scope, "fy_scope": c.fy_scope, "shipment_scope": c.shipment_scope, "components": c.components, "number_width": c.number_width, "start_number": c.start_number, "step": c.step,
            "reset_policy": c.reset_policy, "max_length": c.max_length, "doc_code": c.doc_code, "fy_format": c.fy_format, "shipment_codes": c.shipment_codes or {}, "status": c.status,
            "effective_from": c.effective_from.isoformat() if c.effective_from else None, "effective_to": c.effective_to.isoformat() if c.effective_to else None, "version": c.version, "replaces_id": c.replaces_id,
            "is_system_default": c.is_system_default, "remarks": c.remarks, "updated_at": c.updated_at.isoformat() if c.updated_at else None, "next_number": nxt, "total_length": len(nxt) if nxt else None,
            "prefix": next((x.get("v") for x in c.components if x.get("k") == "prefix"), ""), "suffix": next((x.get("v") for x in c.components if x.get("k") == "suffix"), "")}


def _get(db: Session, user: User, cid: int, lock: bool = False) -> SequenceConfig:
    q = select(SequenceConfig).where(SequenceConfig.id == cid, SequenceConfig.organization_id == _org(user))
    c = db.scalar(q.with_for_update() if lock else q)
    if not c:
        raise HTTPException(404, "Sequence not found")
    return c


def _draft_from(db: Session, org: int, data: dict, base: SequenceConfig | None = None) -> sq._Draft:
    i = sq.info(data["doc_type"] if "doc_type" in data else base.doc_type)
    merged = {**(sq.config_snapshot(base) if base else {}), **{k: v for k, v in data.items() if v is not None}}
    merged.setdefault("doc_type", i["doc_type"])
    if merged.get("max_length") is None:
        merged["max_length"] = i["max_length"]
    if not merged.get("doc_code"):
        merged["doc_code"] = None
    for k in ("effective_from", "effective_to"):
        if isinstance(merged.get(k), str):
            merged[k] = date.fromisoformat(merged[k])
    return sq._Draft(merged)


@router.get("/meta")
def meta(user: User = Depends(permission_required("sequence.view")), db: Session = Depends(get_db)):
    org = _org(user)
    return {"doc_types": sq.registry_list(), "components": list(sq.COMPONENT_TYPES), "separators": list(sq.ALLOWED_SEPARATORS), "reset_policies": list(sq.RESET_POLICIES), "fy_formats": sq.FY_FORMATS,
            "shipment_keys": sq.SHIPMENT_KEYS, "shipment_codes": sq.DEFAULT_SHIPMENT_CODES, "branches": sq.branch_codes(db, org), "current_fy": sq.fiscal_year_code(sq.today()), "financial_max": sq.FINANCIAL_MAX,
            "can": {c: has_permission(user, f"sequence.{c}") for c in ("view", "create", "edit", "suggest", "test", "activate", "deactivate", "adjust", "configure_financial", "history", "export")}}


@router.get("")
def list_sequences(q: str | None = Query(default=None, max_length=80), module: str | None = None, branch: str | None = None, fy: str | None = None, shipment: str | None = None, status: str | None = None,
                   limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0), user: User = Depends(permission_required("sequence.view")), db: Session = Depends(get_db)):
    org = _org(user)
    # make sure the legacy-compatible defaults of the integrated document types are visible
    have = {c.doc_type for c in db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == org))}
    created = False
    for k, v in sq.REGISTRY.items():
        if v[5] and k not in have:
            sq.ensure_default(db, org, k)
            created = True
    if created:
        db.commit()
    cond = [SequenceConfig.organization_id == org]
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(SequenceConfig.name.ilike(like), SequenceConfig.doc_type.ilike(like), SequenceConfig.module.ilike(like)))
    for col, val in ((SequenceConfig.module, module), (SequenceConfig.branch_scope, branch), (SequenceConfig.fy_scope, fy), (SequenceConfig.shipment_scope, shipment), (SequenceConfig.status, status)):
        if val:
            cond.append(col == val)
    total = db.scalar(select(func.count()).select_from(SequenceConfig).where(*cond)) or 0
    rows = db.scalars(select(SequenceConfig).where(*cond).order_by(SequenceConfig.module, SequenceConfig.doc_type, SequenceConfig.id).limit(limit).offset(offset)).all()
    return {"total": total, "items": [_out(db, c) for c in rows]}


@router.post("", status_code=201)
def create_sequence(body: ConfigIn, user: User = Depends(permission_required("sequence.create")), db: Session = Depends(get_db)):
    org = _org(user)
    _fin_guard(user, body.doc_type)
    data = _norm(body.model_dump())
    info = sq.info(data["doc_type"])
    data["module"] = info["module"]
    if data.get("max_length") is None:
        data["max_length"] = info["max_length"]
    if not data.get("components"):
        data["components"] = sq.default_components(data["doc_type"])
    c = SequenceConfig(organization_id=org, status="DRAFT", created_by=user.id, updated_by=user.id, version=1, is_system_default=False, **data)
    db.add(c)
    db.flush()
    sq.event(db, org, c.id, "SEQUENCE_CREATED", user, doc_type=c.doc_type, new=sq.config_snapshot(c))
    db.commit()
    return {**_out(db, c), "validation": sq.validate(db, org, c)}


@router.get("/history/export")
def export_history(user: User = Depends(permission_required("sequence.export")), db: Session = Depends(get_db)):
    org = _org(user)
    rows = db.scalars(select(SequenceEvent).where(SequenceEvent.organization_id == org).order_by(SequenceEvent.id)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Event ID", "Time (UTC)", "Sequence ID", "Document Type", "Action", "Result", "User", "Reason", "Related", "Previous", "New"])
    safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v
    for e in rows:
        w.writerow([e.id, e.created_at.isoformat(), e.config_id, e.doc_type, e.action, e.result, safe(e.actor_name), safe(e.reason), e.related_ref, str(e.previous or ""), str(e.new or "")])
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="sequence-history.csv"'})


@router.get("/export")
def export_config(user: User = Depends(permission_required("sequence.export")), db: Session = Depends(get_db)):
    org = _org(user)
    rows = db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == org).order_by(SequenceConfig.module, SequenceConfig.doc_type, SequenceConfig.id)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Sequence", "Module", "Document Type", "Branch", "Financial Year", "Shipment Type", "Prefix", "Suffix", "Next Number", "Width", "Total Length", "Max Length", "Reset", "Status", "Version", "Updated"])
    safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v
    for c in rows:
        o = _out(db, c)
        w.writerow([safe(o["name"]), o["module"], o["doc_type"], o["branch_scope"], o["fy_scope"], o["shipment_scope"], o["prefix"], o["suffix"], o["next_number"], o["number_width"], o["total_length"], o["max_length"], o["reset_policy"], o["status"], o["version"], o["updated_at"]])
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="sequence-configuration.csv"'})


@router.post("/preview")
def preview(body: ConfigIn, user: User = Depends(permission_required("sequence.test")), db: Session = Depends(get_db)):
    """Live preview of an UNSAVED configuration with simulated numbers: nothing is consumed or reserved."""
    org = _org(user)
    data = _norm(body.model_dump())
    d = _draft_from(db, org, data)
    v = sq.validate(db, org, d)
    return {"validation": v, "samples": sq.sample_numbers(db, org, d, 5) if not v["errors"] or d.components else [], "branch_used": d.branch_scope if d.branch_scope != "ALL" else (sq.branch_codes(db, org) or [None])[0],
            "financial_year": sq.fy_text(sq.today(), d.fy_format) if d.fy_format in sq.FY_FORMATS else None, "max_length": d.max_length}


@router.post("/suggest")
def suggest(body: SuggestIn, user: User = Depends(permission_required("sequence.suggest")), db: Session = Depends(get_db)):
    org = _org(user)
    items = sq.suggestions(db, org, body.doc_type)
    sq.event(db, org, None, "SUGGESTIONS_GENERATED", user, doc_type=body.doc_type, new={"count": len(items)})
    db.commit()
    return {"doc_type": body.doc_type, "info": sq.info(body.doc_type), "suggestions": items}


@router.post("/issue")
def issue_for_crm(body: IssueIn, user: User = Depends(permission_required("quote.create")), db: Session = Depends(get_db)):
    """Number for records that are numbered at save time in the browser (CRM leads, enquiries, opportunities ...)."""
    org = _org(user)
    if body.doc_type not in sq.CRM_TYPES:
        raise HTTPException(403, "This document type is numbered by its own screen")
    number = sq.issue(db, org, body.doc_type, user=user, branch_code=body.branch, related_ref="crm")
    db.commit()
    return {"number": number, "doc_type": body.doc_type}


@router.get("/{cid}")
def get_sequence(cid: int, user: User = Depends(permission_required("sequence.view")), db: Session = Depends(get_db)):
    c = _get(db, user, cid)
    return {**_out(db, c), "validation": sq.validate(db, c.organization_id, c)}


@router.patch("/{cid}")
def edit_sequence(cid: int, body: ConfigPatch, user: User = Depends(permission_required("sequence.edit")), db: Session = Depends(get_db)):
    c = _get(db, user, cid, lock=True)
    _fin_guard(user, c.doc_type)
    changes = _norm(body.model_dump(exclude_unset=True))
    if c.status == "RETIRED":
        raise HTTPException(409, "A retired sequence is kept for history and cannot be edited")
    if c.status != "DRAFT":
        material = set(changes) - {"name", "remarks"}
        if material:
            raise HTTPException(409, "An activated sequence cannot be changed in place, so issued numbers never change retrospectively. Use 'New version' to prepare the change, then activate it.")
    before = sq.config_snapshot(c)
    for k, v in changes.items():
        setattr(c, k, v)
    c.updated_by = user.id
    sq.event(db, c.organization_id, c.id, "SEQUENCE_EDITED", user, doc_type=c.doc_type, previous=before, new=sq.config_snapshot(c))
    db.commit()
    return {**_out(db, c), "validation": sq.validate(db, c.organization_id, c)}


@router.post("/{cid}/new-version", status_code=201)
def new_version(cid: int, user: User = Depends(permission_required("sequence.create")), db: Session = Depends(get_db)):
    src = _get(db, user, cid)
    _fin_guard(user, src.doc_type)
    latest = db.scalar(select(func.max(SequenceConfig.version)).where(SequenceConfig.organization_id == src.organization_id, SequenceConfig.doc_type == src.doc_type, SequenceConfig.branch_scope == src.branch_scope,
                                                                   SequenceConfig.fy_scope == src.fy_scope, SequenceConfig.shipment_scope == src.shipment_scope)) or src.version
    snap = sq.config_snapshot(src)
    c = SequenceConfig(organization_id=src.organization_id, module=src.module, status="DRAFT", created_by=user.id, updated_by=user.id, version=latest + 1, replaces_id=src.id, is_system_default=False,
                       name=src.name.replace(" (default)", "") + f" v{latest + 1}", doc_type=src.doc_type, branch_scope=src.branch_scope, fy_scope=src.fy_scope, shipment_scope=src.shipment_scope, components=list(src.components),
                       number_width=src.number_width, start_number=src.start_number, step=src.step, reset_policy=src.reset_policy, max_length=src.max_length, doc_code=src.doc_code, fy_format=src.fy_format,
                       shipment_codes=dict(src.shipment_codes or {}), remarks=src.remarks)
    db.add(c)
    db.flush()
    sq.event(db, c.organization_id, c.id, "SEQUENCE_VERSION_CREATED", user, doc_type=c.doc_type, previous=snap, new=sq.config_snapshot(c), related_ref=f"replaces {src.id}")
    db.commit()
    return _out(db, c)


def _same_scope(a: SequenceConfig, b: SequenceConfig) -> bool:
    return (a.branch_scope, a.fy_scope, a.shipment_scope) == (b.branch_scope, b.fy_scope, b.shipment_scope)


def _overlap(a: SequenceConfig, b: SequenceConfig) -> bool:
    af, at, bf, bt = a.effective_from or date.min, a.effective_to or date.max, b.effective_from or date.min, b.effective_to or date.max
    return af <= bt and bf <= at


@router.post("/{cid}/activate")
def activate(cid: int, body: Reason | None = None, user: User = Depends(permission_required("sequence.activate")), db: Session = Depends(get_db)):
    c = _get(db, user, cid, lock=True)
    org = c.organization_id
    _fin_guard(user, c.doc_type)
    if c.status == "ACTIVE":
        raise HTTPException(409, "This sequence is already active")
    if c.status == "RETIRED":
        raise HTTPException(409, "A retired sequence cannot be activated again; create a new version")
    v = sq.validate(db, org, c, True)
    if v["errors"]:
        sq.event(db, org, c.id, "ACTIVATION_REFUSED", user, doc_type=c.doc_type, new={"errors": v["errors"]}, result="FAILED")
        db.commit()
        raise HTTPException(422, {"message": "The configuration is not valid and cannot be activated", "errors": v["errors"]})
    others = [o for o in db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == org, SequenceConfig.doc_type == c.doc_type, SequenceConfig.status == "ACTIVE", SequenceConfig.id != c.id))
              if _same_scope(o, c) and _overlap(o, c)]
    replaced = []
    for o in others:
        if o.is_system_default or o.id == c.replaces_id:  # the default, or the version being replaced, steps aside
            o.status = "INACTIVE" if o.id != c.replaces_id else "RETIRED"
            o.updated_by = user.id
            sq.event(db, org, o.id, "SEQUENCE_DEACTIVATED", user, doc_type=o.doc_type, reason=f"Replaced by '{c.name}'", new={"status": o.status})
            replaced.append(o.name)
        else:
            sq.event(db, org, c.id, "CONFLICT_DETECTED", user, doc_type=c.doc_type, new={"conflicts_with": o.id}, result="FAILED")
            db.commit()
            raise HTTPException(409, f"Another active sequence ('{o.name}') already covers the same scope. Deactivate it first, or use 'New version' of that sequence to replace it.")
    c.status = "ACTIVE"
    c.effective_from = c.effective_from or sq.today()
    c.updated_by = user.id
    sq.event(db, org, c.id, "SEQUENCE_ACTIVATED", user, doc_type=c.doc_type, reason=(body.reason if body else None), new={**sq.config_snapshot(c), "replaced": replaced})
    record_audit(db, "SEQUENCE_ACTIVATED", "SETTINGS", user, org, "SequenceConfig", str(c.id), {"doc_type": c.doc_type, "name": c.name})
    db.commit()
    return {**_out(db, c), "validation": v, "replaced": replaced}


@router.post("/{cid}/deactivate")
def deactivate(cid: int, body: Reason, user: User = Depends(permission_required("sequence.deactivate")), db: Session = Depends(get_db)):
    c = _get(db, user, cid, lock=True)
    _fin_guard(user, c.doc_type)
    if c.status != "ACTIVE":
        raise HTTPException(409, "Only an active sequence can be deactivated")
    if not (body.reason or "").strip():
        raise HTTPException(422, "Give a reason for deactivating the sequence")
    c.status = "INACTIVE"
    c.updated_by = user.id
    sq.event(db, c.organization_id, c.id, "SEQUENCE_DEACTIVATED", user, doc_type=c.doc_type, reason=body.reason.strip(), new={"status": "INACTIVE"})
    db.commit()
    return _out(db, c)


@router.post("/{cid}/retire")
def retire(cid: int, body: Reason, user: User = Depends(permission_required("sequence.deactivate")), db: Session = Depends(get_db)):
    c = _get(db, user, cid, lock=True)
    _fin_guard(user, c.doc_type)
    if c.status == "ACTIVE":
        raise HTTPException(409, "Deactivate the sequence before retiring it")
    if c.status == "RETIRED":
        raise HTTPException(409, "Already retired")
    c.status = "RETIRED"
    c.updated_by = user.id
    sq.event(db, c.organization_id, c.id, "SEQUENCE_RETIRED", user, doc_type=c.doc_type, reason=(body.reason or "").strip() or None, new={"status": "RETIRED"})
    db.commit()
    return _out(db, c)


@router.post("/{cid}/test")
def test_sequence(cid: int, user: User = Depends(permission_required("sequence.test")), db: Session = Depends(get_db)):
    """Diagnostic run against the saved configuration and its real counter, without issuing anything."""
    c = _get(db, user, cid)
    v = sq.validate(db, c.organization_id, c)
    nxt = sq.next_preview(db, c)
    first = None
    if nxt is not None:
        period = sq.period_key(c, sq.today())
        counter = db.scalar(select(SequenceCounter).where(SequenceCounter.config_id == c.id, SequenceCounter.period_key == period))
        last = counter.last_number if counter and counter.last_number != -1 else c.start_number - c.step
        first = last + c.step
    conflict = []
    if c.status == "ACTIVE":
        for o in db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == c.organization_id, SequenceConfig.doc_type == c.doc_type, SequenceConfig.status == "ACTIVE", SequenceConfig.id != c.id)):
            if _same_scope(o, c) and _overlap(o, c):
                conflict.append(o.name)
    return {"validation": v, "samples": sq.sample_numbers(db, c.organization_id, c, 5, first=first), "conflicts": conflict, "consumed": False,
            "uniqueness": "unique" if all(not sq.exists_in_business(db, c.organization_id, c.doc_type, s["number"]) for s in sq.sample_numbers(db, c.organization_id, c, 5, first=first) if s["number"]) else "collides with an existing record"}


@router.post("/{cid}/adjust-next")
def adjust_next(cid: int, body: AdjustIn, user: User = Depends(permission_required("sequence.adjust")), db: Session = Depends(get_db)):
    """Move the next number forward (never backwards, never onto an issued identifier). Recorded with the reason."""
    c = _get(db, user, cid, lock=True)
    _fin_guard(user, c.doc_type)
    if c.status == "RETIRED":
        raise HTTPException(409, "A retired sequence cannot be adjusted")
    period = body.period or sq.period_key(c, sq.today())
    counter = sq._counter(db, c, period)
    current_next = counter.last_number + c.step
    if body.next_number < current_next:
        raise HTTPException(409, f"The next number can only move forward. The current next number is {current_next}; going lower could reuse an issued identifier.")
    if (body.next_number - c.start_number) % c.step:
        raise HTTPException(422, f"The next number must follow the increment step of {c.step} from the start number {c.start_number}")
    if body.next_number > 10 ** c.number_width - 1:
        raise HTTPException(422, "The next number does not fit in the number width")
    s = sq.render(c, body.next_number, sq.today(), (c.branch_scope if c.branch_scope != "ALL" else (sq.branch_codes(db, c.organization_id) or [None])[0]), "Sea Export" if any(x.get("k") == "shipment" for x in c.components) else None)
    if len(s) > c.max_length:
        raise HTTPException(422, f"That number would make the identifier {len(s)} characters (maximum {c.max_length})")
    previous = {"next_number": current_next, "period": period}
    counter.last_number = body.next_number - c.step
    sq.event(db, c.organization_id, c.id, "NEXT_NUMBER_ADJUSTED", user, doc_type=c.doc_type, previous=previous, new={"next_number": body.next_number, "period": period}, reason=body.reason.strip())
    record_audit(db, "SEQUENCE_NEXT_ADJUSTED", "SETTINGS", user, c.organization_id, "SequenceConfig", str(c.id), {"from": current_next, "to": body.next_number})
    db.commit()
    return {**_out(db, c), "adjusted_from": current_next}


@router.get("/{cid}/history")
def history(cid: int, limit: int = Query(default=100, ge=1, le=500), user: User = Depends(permission_required("sequence.history")), db: Session = Depends(get_db)):
    c = _get(db, user, cid)
    ev = db.scalars(select(SequenceEvent).where(SequenceEvent.organization_id == c.organization_id, SequenceEvent.config_id == c.id).order_by(SequenceEvent.id.desc()).limit(limit)).all()
    issued = db.scalars(select(IssuedNumber).where(IssuedNumber.config_id == c.id).order_by(IssuedNumber.id.desc()).limit(50)).all()
    return {"events": [{"id": e.id, "action": e.action, "result": e.result, "user": e.actor_name, "at": e.created_at.isoformat(), "reason": e.reason, "previous": e.previous, "new": e.new, "related_ref": e.related_ref} for e in ev],
            "issued": [{"number": n.number, "at": n.issued_at.isoformat(), "branch": n.branch_code, "shipment": n.shipment_key, "related": f"{n.related_type or ''} {n.related_id or ''}".strip(), "version": n.config_version} for n in issued],
            "issued_total": db.scalar(select(func.count()).select_from(IssuedNumber).where(IssuedNumber.config_id == c.id)) or 0}
