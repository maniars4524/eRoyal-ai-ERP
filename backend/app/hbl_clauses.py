"""HBL Clause Master (Masters > Sea Export > HBL Clause Master) and the clause selection of a House BL.

A clause is maintained centrally; the user picks clauses on the job's BL tab; the SELECTION IS SNAPSHOTTED (text included) on the BL, so later master edits never change a saved / released HBL.
The BL stages are the existing Draft -> Confirmed (approval) -> Released. Clause changes are allowed in Draft only; a Confirmed BL must be returned to Draft first (re-approval), a Released one is locked."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from . import job_lifecycle as jl
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import HblClause, JobDetail, Shipment, User

router = APIRouter(prefix="/api/hbl-clauses", tags=["hbl clauses"])
job_router = APIRouter(prefix="/api/jobs", tags=["hbl clauses"])

CATEGORIES = ("Freight", "Container", "Cargo", "Delivery", "Customs", "Documentation", "Liability", "Charges", "Release", "Other")
SHIPMENT_TYPES = ("FCL", "LCL", "Both")
MOVEMENTS = ("All", "CY/CY", "CY/CFS", "CFS/CY", "CFS/CFS", "Door Delivery", "Port-to-Port", "Door-to-Door")
FREIGHT_TERMS = ("Prepaid", "Collect", "Both")

# the user's single list of sample clauses: (title/text, category, shipment type, movement, freight term, exclusive group)
SAMPLE = [
    ("ACD PREPAID, DDC COLLECT", "Freight", "Both", "All", "Both", "freight-term"), ("CY/CY", "Container", "Both", "CY/CY", "Both", "movement"), ("EXPRESS RELEASE", "Release", "Both", "All", "Both", "release-type"),
    ("FCL/FCL", "Container", "FCL", "All", "Both", "load-type"), ("LCL/LCL", "Container", "LCL", "All", "Both", "load-type"), ("CFS/CFS", "Container", "Both", "CFS/CFS", "Both", "movement"),
    ("CFS/CY", "Container", "Both", "CFS/CY", "Both", "movement"), ("CY/CFS", "Container", "Both", "CY/CFS", "Both", "movement"), ("FREIGHT PREPAID", "Freight", "Both", "All", "Prepaid", "freight-term"),
    ("FREIGHT COLLECT", "Freight", "Both", "All", "Collect", "freight-term"), ("ORIGINAL BILL OF LADING REQUIRED", "Release", "Both", "All", "Both", "release-type"), ("SEA WAYBILL", "Release", "Both", "All", "Both", "release-type"),
    ("SHIPPER'S LOAD, STOW AND COUNT", "Cargo", "FCL", "All", "Both", ""), ("SAID TO CONTAIN", "Cargo", "Both", "All", "Both", ""), ("SHIPPER-DECLARED WEIGHT", "Cargo", "Both", "All", "Both", ""),
    ("SHIPPER-DECLARED CARGO DESCRIPTION", "Cargo", "Both", "All", "Both", ""), ("SHIPPER-DECLARED QUANTITY AND MEASUREMENTS", "Cargo", "Both", "All", "Both", ""),
    ("CONTAINER AND SEAL NUMBERS AS DECLARED BY SHIPPER", "Container", "FCL", "All", "Both", ""),
    ("FOR ALL DOOR AND HOUSE DELIVERIES, STANDARD UNLOADING FREE TIME AS PREVAILING AT DESTINATION WILL APPLY. ALL CHARGES AFTER EXPIRY OF FREE TIME WILL BE FOR THE ACCOUNT OF THE CONSIGNEE/RECEIVER.", "Delivery", "Both", "Door Delivery", "Both", ""),
    ("ALL DESTINATION CHARGES TO BE BORNE BY THE CONSIGNEE UNLESS OTHERWISE AGREED.", "Charges", "Both", "All", "Both", ""), ("ALL ORIGIN CHARGES TO BE BORNE BY THE SHIPPER UNLESS OTHERWISE AGREED.", "Charges", "Both", "All", "Both", ""),
    ("DESTINATION TERMINAL HANDLING CHARGES COLLECT.", "Charges", "Both", "All", "Both", ""), ("ORIGIN TERMINAL HANDLING CHARGES PREPAID.", "Charges", "Both", "All", "Both", ""),
    ("CUSTOMS CLEARANCE AT DESTINATION FOR CONSIGNEE'S ACCOUNT.", "Customs", "Both", "All", "Both", ""), ("CUSTOMS DUTIES AND TAXES AT DESTINATION FOR CONSIGNEE'S ACCOUNT.", "Customs", "Both", "All", "Both", ""),
    ("ALL STORAGE CHARGES AT DESTINATION FOR THE ACCOUNT OF THE RESPONSIBLE PARTY.", "Charges", "Both", "All", "Both", ""), ("DEMURRAGE AND DETENTION CHARGES AS PER APPLICABLE TARIFF.", "Charges", "Both", "All", "Both", ""),
    ("DELIVERY SUBJECT TO PAYMENT OF ALL APPLICABLE CHARGES.", "Delivery", "Both", "All", "Both", ""), ("DELIVERY SUBJECT TO CUSTOMS CLEARANCE AND GOVERNMENT APPROVAL.", "Delivery", "Both", "All", "Both", ""),
    ("SHIPPER IS RESPONSIBLE FOR ACCURACY OF CARGO PARTICULARS.", "Cargo", "Both", "All", "Both", ""), ("CARGO DESCRIPTION, WEIGHT AND MEASUREMENTS AS DECLARED BY SHIPPER.", "Cargo", "Both", "All", "Both", ""),
    ("CARGO INSURANCE NOT ARRANGED BY THE CARRIER UNLESS OTHERWISE AGREED.", "Liability", "Both", "All", "Both", ""), ("CARGO TO BE PROPERLY PACKED AND MARKED BY THE SHIPPER.", "Cargo", "Both", "All", "Both", ""),
    ("TRANSSHIPMENT PERMITTED.", "Documentation", "Both", "All", "Both", ""), ("PART LOAD SHIPMENT.", "Cargo", "Both", "All", "Both", ""), ("FULL CONTAINER LOAD SHIPMENT.", "Container", "FCL", "All", "Both", "load-type"),
    ("LESS THAN CONTAINER LOAD SHIPMENT.", "Container", "LCL", "All", "Both", "load-type"), ("SHIPPER-OWNED CONTAINER (SOC).", "Container", "FCL", "All", "Both", "container-owner"), ("CARRIER-OWNED CONTAINER (COC).", "Container", "FCL", "All", "Both", "container-owner"),
    ("NON-STACKABLE CARGO.", "Cargo", "Both", "All", "Both", ""), ("PALLETIZED CARGO.", "Cargo", "Both", "All", "Both", ""), ("TEMPERATURE-CONTROLLED CARGO AS PER SHIPPER'S INSTRUCTIONS.", "Cargo", "Both", "All", "Both", ""),
    ("DANGEROUS GOODS TO BE DECLARED IN ACCORDANCE WITH APPLICABLE REGULATIONS.", "Cargo", "Both", "All", "Both", ""), ("UNDECLARED OR MISDECLARED CARGO SUBJECT TO APPLICABLE TERMS AND CONDITIONS.", "Liability", "Both", "All", "Both", ""),
    ("CARGO RECEIVED IN APPARENT GOOD ORDER AND CONDITION, UNLESS OTHERWISE NOTED.", "Cargo", "Both", "All", "Both", ""), ("CARRIER'S LIABILITY SUBJECT TO THE TERMS AND CONDITIONS OF THIS BILL OF LADING AND APPLICABLE LAW.", "Liability", "Both", "All", "Both", ""),
    ("FORCE MAJEURE CLAUSE APPLICABLE AS PER THE TERMS AND CONDITIONS OF THIS BILL OF LADING.", "Liability", "Both", "All", "Both", ""), ("ALL SHIPMENTS SUBJECT TO THE TERMS AND CONDITIONS OF THIS HOUSE BILL OF LADING.", "Documentation", "Both", "All", "Both", ""),
    ("DELIVERY ORDER RELEASE SUBJECT TO COMPLETION OF APPLICABLE DOCUMENTATION REQUIREMENTS.", "Release", "Both", "All", "Both", ""), ("WAITING TIME AND ADDITIONAL DELIVERY CHARGES FOR CONSIGNEE'S ACCOUNT WHERE APPLICABLE.", "Delivery", "Both", "Door Delivery", "Both", ""),
    ("REDELIVERY CHARGES FOR CONSIGNEE'S ACCOUNT WHERE APPLICABLE.", "Delivery", "Both", "Door Delivery", "Both", ""), ("ADDRESS CORRECTION AND DELIVERY RESCHEDULING CHARGES FOR CONSIGNEE'S ACCOUNT WHERE APPLICABLE.", "Delivery", "Both", "Door Delivery", "Both", ""),
    ("CARGO EXAMINATION AND INSPECTION CHARGES FOR THE ACCOUNT OF THE RESPONSIBLE PARTY.", "Customs", "Both", "All", "Both", ""), ("PORT STORAGE AND WAREHOUSE CHARGES AS PER APPLICABLE TARIFF.", "Charges", "Both", "All", "Both", ""),
    ("CARGO RELEASE SUBJECT TO RECEIPT OF VALID SHIPPING DOCUMENTS.", "Release", "Both", "All", "Both", ""), ("SHIPMENT SUBJECT TO APPLICABLE PORT, CUSTOMS AND GOVERNMENT REGULATIONS.", "Customs", "Both", "All", "Both", ""),
    ("FREIGHT AND CHARGES PAYABLE AS PER AGREED TERMS.", "Freight", "Both", "All", "Both", ""), ("ALL ADDITIONAL CHARGES ARISING DUE TO INCORRECT SHIPPER-DECLARED PARTICULARS FOR THE RESPONSIBLE PARTY'S ACCOUNT.", "Charges", "Both", "All", "Both", ""),
    ("DELIVERY TO THE NAMED PLACE OF DELIVERY SUBJECT TO ACCESSIBILITY AND LOCAL DELIVERY RESTRICTIONS.", "Delivery", "Both", "Door Delivery", "Both", ""), ("JURISDICTION AND GOVERNING LAW AS STATED IN THE TERMS AND CONDITIONS OF THIS BILL OF LADING.", "Liability", "Both", "All", "Both", ""),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper().rstrip(".")


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Clauses are company data; the platform administrator has none")
    return user.organization_id


def clause_out(c: HblClause, names: dict | None = None) -> dict:
    names = names or {}
    return {"id": c.id, "code": c.code, "title": c.title, "text": c.text, "category": c.category, "shipment_type": c.shipment_type, "movement_type": c.movement_type, "country": c.country or "", "pol": c.pol or "", "pod": c.pod or "",
            "freight_term": c.freight_term, "is_default": c.is_default, "is_mandatory": c.is_mandatory, "display_order": c.display_order, "exclusive_group": c.exclusive_group or "", "status": c.status,
            "created_by": names.get(c.created_by), "created_at": c.created_at, "updated_by": names.get(c.updated_by), "updated_at": c.updated_at}


class ClauseIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    text: str = Field(min_length=2, max_length=4000)
    category: str = Field(default="Other", pattern="^(" + "|".join(CATEGORIES) + ")$")
    shipment_type: str = Field(default="Both", pattern="^(FCL|LCL|Both)$")
    movement_type: str = Field(default="All", pattern="^(" + "|".join(re.escape(m) for m in MOVEMENTS) + ")$")
    country: str = Field(default="", max_length=80)
    pol: str = Field(default="", max_length=80)
    pod: str = Field(default="", max_length=80)
    freight_term: str = Field(default="Both", pattern="^(Prepaid|Collect|Both)$")
    is_default: bool = False
    is_mandatory: bool = False
    display_order: int = Field(default=100, ge=0, le=100000)
    exclusive_group: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9._-]*$")
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")


class ClausePatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    text: str | None = Field(default=None, min_length=2, max_length=4000)
    category: str | None = Field(default=None, pattern="^(" + "|".join(CATEGORIES) + ")$")
    shipment_type: str | None = Field(default=None, pattern="^(FCL|LCL|Both)$")
    movement_type: str | None = Field(default=None, pattern="^(" + "|".join(re.escape(m) for m in MOVEMENTS) + ")$")
    country: str | None = Field(default=None, max_length=80)
    pol: str | None = Field(default=None, max_length=80)
    pod: str | None = Field(default=None, max_length=80)
    freight_term: str | None = Field(default=None, pattern="^(Prepaid|Collect|Both)$")
    is_default: bool | None = None
    is_mandatory: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=100000)
    exclusive_group: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9._-]*$")
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


def _next_code(db: Session, org_id: int) -> str:
    top = 0
    for (code,) in db.execute(select(HblClause.code).where(HblClause.organization_id == org_id)):
        m = re.search(r"(\d+)$", code or "")
        top = max(top, int(m.group(1)) if m else 0)
    return f"HBLC-{top + 1:04d}"


def _names(db: Session, rows) -> dict:
    ids = {x for c in rows for x in (c.created_by, c.updated_by) if x}
    return {u.id: (((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username) for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


@router.get("")
def list_clauses(q: str = "", category: str = "", status: str = "", shipment_type: str = "", limit: int = Query(default=25, ge=1, le=500), offset: int = Query(default=0, ge=0),
                 user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    org = _tenant(user)
    cond = [HblClause.organization_id == org]
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(HblClause.code.ilike(like), HblClause.title.ilike(like), HblClause.text.ilike(like)))
    if category:
        cond.append(HblClause.category == category)
    if status:
        cond.append(HblClause.status == status)
    if shipment_type:
        cond.append(HblClause.shipment_type == shipment_type)
    from sqlalchemy import func

    total = db.scalar(select(func.count()).select_from(HblClause).where(*cond)) or 0
    rows = list(db.scalars(select(HblClause).where(*cond).order_by(HblClause.display_order, HblClause.id).limit(limit).offset(offset)))
    names = _names(db, rows)
    stats = {"total": db.scalar(select(func.count()).select_from(HblClause).where(HblClause.organization_id == org)) or 0,
             "active": db.scalar(select(func.count()).select_from(HblClause).where(HblClause.organization_id == org, HblClause.status == "ACTIVE")) or 0,
             "mandatory": db.scalar(select(func.count()).select_from(HblClause).where(HblClause.organization_id == org, HblClause.is_mandatory.is_(True), HblClause.status == "ACTIVE")) or 0,
             "default": db.scalar(select(func.count()).select_from(HblClause).where(HblClause.organization_id == org, HblClause.is_default.is_(True), HblClause.status == "ACTIVE")) or 0}
    return {"items": [clause_out(c, names) for c in rows], "total": total, "stats": stats, "categories": CATEGORIES, "movements": MOVEMENTS}


@router.post("", status_code=201)
def create_clause(body: ClauseIn, user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    org = _tenant(user)
    if any(_norm(c.text) == _norm(body.text) for c in db.scalars(select(HblClause).where(HblClause.organization_id == org))):
        raise HTTPException(409, "A clause with the same wording already exists")
    c = HblClause(organization_id=org, code=_next_code(db, org), created_by=user.id, **body.model_dump())
    db.add(c)
    db.flush()
    record_audit(db, "HBL_CLAUSE_CREATED", "MASTER", user, org, "HblClause", str(c.id), {"code": c.code, "title": c.title})
    db.commit()
    return clause_out(c, _names(db, [c]))


@router.patch("/{clause_id}")
def update_clause(clause_id: int, body: ClausePatch, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    org = _tenant(user)
    c = db.scalar(select(HblClause).where(HblClause.id == clause_id, HblClause.organization_id == org))
    if not c:
        raise HTTPException(404, "Clause not found")
    changes = body.model_dump(exclude_unset=True)
    if "text" in changes and any(o.id != c.id and _norm(o.text) == _norm(changes["text"]) for o in db.scalars(select(HblClause).where(HblClause.organization_id == org))):
        raise HTTPException(409, "A clause with the same wording already exists")
    before = {k: getattr(c, k) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v)
    c.updated_by = user.id
    record_audit(db, "HBL_CLAUSE_UPDATED", "MASTER", user, org, "HblClause", str(c.id), {"code": c.code, "before": {k: str(v)[:200] for k, v in before.items()}, "after": {k: str(v)[:200] for k, v in changes.items()}})
    db.commit()
    return clause_out(c, _names(db, [c]))


@router.post("/load-sample")
def load_sample(user: User = Depends(permission_required("master.create")), db: Session = Depends(get_db)):
    """Load the sample clause list (additive: wording that already exists is skipped). Nothing is mandatory or default: the administrator decides."""
    org = _tenant(user)
    have = {_norm(c.text) for c in db.scalars(select(HblClause).where(HblClause.organization_id == org))}
    made = skipped = 0
    for i, (text, cat, st, mv, ft, grp) in enumerate(SAMPLE, start=1):
        if _norm(text) in have:
            skipped += 1
            continue
        title = text if len(text) <= 60 else text[:57].rstrip() + "..."
        db.add(HblClause(organization_id=org, code=_next_code(db, org), title=title, text=text, category=cat, shipment_type=st, movement_type=mv, freight_term=ft, exclusive_group=grp, display_order=i * 10, created_by=user.id))
        db.flush()
        have.add(_norm(text))
        made += 1
    record_audit(db, "HBL_CLAUSES_SAMPLE_LOADED", "MASTER", user, org, "HblClause", "", {"created": made, "skipped": skipped})
    db.commit()
    return {"created": made, "skipped": skipped}


# ------------------------------------------------------------------ applicability and selection on a job
def _has(value: str, text: str) -> bool:
    return not (value or "").strip() or (value or "").strip().lower() in (text or "").lower()


def applicable(c: HblClause, job: Shipment, bl: dict, booking_country: str = "") -> bool:
    """Does the clause fit this shipment (FCL / LCL, freight term, POL, POD, country)? Blank conditions always fit."""
    mode = (job.container_mode or "").upper()
    if c.shipment_type in ("FCL", "LCL") and mode in ("FCL", "LCL") and c.shipment_type != mode:
        return False
    term = (bl.get("terms") or "").title()
    if c.freight_term in ("Prepaid", "Collect") and term in ("Prepaid", "Collect") and c.freight_term != term:
        return False
    move = (bl.get("movement") or "").upper()
    if c.movement_type not in ("All", "Door Delivery", "Port-to-Port", "Door-to-Door") and move and c.movement_type.upper() != move:
        return False
    return _has(c.pol, job.pol or "") and _has(c.pod, job.pod or "") and _has(c.country, " ".join([job.pod or "", booking_country]))


def _state(db: Session, job: Shipment, row: JobDetail | None) -> dict:
    bl = dict((row.bl if row else {}) or {})
    stage = bl.get("blStage") or "Draft"
    return {"bl": bl, "stage": stage}


def validate_selection(db: Session, job: Shipment, bl: dict, selected: list[dict]) -> list[str]:
    """Problems that must be fixed before the BL is confirmed (approved) or released. Nothing is changed silently."""
    org = job.organization_id
    clauses = list(db.scalars(select(HblClause).where(HblClause.organization_id == org, HblClause.status == "ACTIVE")))
    mandatory = [c for c in clauses if c.is_mandatory and applicable(c, job, bl)]
    msgs: list[str] = []
    if not mandatory and not selected and not bl.get("clausesSavedAt"):
        return msgs      # a company that has not set up clauses keeps working exactly as before
    if mandatory and not bl.get("clausesSavedAt"):
        msgs.append("Save the HBL clause selection first (the mandatory clauses must be on the BL)")
    ids = {s.get("clause_id") for s in selected}
    for c in mandatory:
        if c.id not in ids and not any(_norm(s.get("text")) == _norm(c.text) for s in selected):
            msgs.append(f"Mandatory clause {c.code} is missing: {c.title}")
    groups: dict[str, list[str]] = {}
    for s in selected:
        if s.get("group"):
            groups.setdefault(s["group"], []).append(s.get("code") or s.get("title") or "")
    for g, codes in groups.items():
        if len(codes) > 1:
            msgs.append(f"Conflicting clauses ({g}): " + ", ".join(codes) + " cannot be on the same HBL")
    return msgs


def check_before_stage(db: Session, job: Shipment, row: JobDetail | None, bl: dict) -> None:
    """Called when the BL is confirmed or released."""
    msgs = validate_selection(db, job, bl, list(bl.get("clauses") or []))
    if msgs:
        raise HTTPException(422, "HBL clauses: " + "; ".join(msgs))


class PickIn(BaseModel):
    clause_id: int | None = None
    title: str = Field(default="", max_length=160)
    text: str = Field(default="", max_length=4000)


class SelectionIn(BaseModel):
    items: list[PickIn] = Field(default_factory=list, max_length=100)
    override_reason: str = Field(default="", max_length=300)


@job_router.get("/{job_number}/hbl-clauses")
def get_selection(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    from .job_details_api import _job, _row

    job = _job(db, user, job_number)
    row = _row(db, job)
    st = _state(db, job, row)
    bl = st["bl"]
    clauses = list(db.scalars(select(HblClause).where(HblClause.organization_id == job.organization_id, HblClause.status == "ACTIVE").order_by(HblClause.display_order, HblClause.id)))
    fit = [c for c in clauses if applicable(c, job, bl)]
    selected = list(bl.get("clauses") or [])
    suggested = [] if bl.get("clausesSavedAt") else [clause_out(c) for c in fit if c.is_mandatory or c.is_default]
    return {"job_number": job.job_number, "hbl_number": bl.get("hblNo") or bl.get("directNo") or "", "stage": st["stage"], "locked": st["stage"] != "Draft", "saved": bool(bl.get("clausesSavedAt")), "saved_at": bl.get("clausesSavedAt"),
            "version": bl.get("clauseVersion") or 0, "selected": selected, "available": [clause_out(c) for c in fit], "others": [clause_out(c) for c in clauses if c not in fit], "suggested": suggested,
            "context": {"shipment_type": job.container_mode, "freight_term": bl.get("terms"), "pol": job.pol, "pod": job.pod, "shipper": (row.parties or {}).get("shipper") if row else "", "consignee": (row.parties or {}).get("consignee") if row else "", "notify": (row.parties or {}).get("notify1") if row else ""},
            "can_custom": has_permission(user, "hbl.clause.custom"), "can_override": has_permission(user, "hbl.clause.override"), "history": list(bl.get("clauseHistory") or [])[-10:], "problems": validate_selection(db, job, bl, selected)}


@job_router.put("/{job_number}/hbl-clauses")
def save_selection(job_number: str, body: SelectionIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    from .job_details_api import _job, _row

    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the HBL clauses")
    row = _row(db, job, create=True)
    bl = dict(row.bl or {})
    stage = bl.get("blStage") or "Draft"
    if stage == "Released":
        raise HTTPException(409, "The BL is released: its clauses are locked")
    if stage == "Confirmed":
        raise HTTPException(409, "The BL is approved (Confirmed): return it to Draft to change the clauses, then approve it again")
    org = job.organization_id
    snapshot: list[dict] = []
    seen_ids: set[int] = set()
    seen_text: set[str] = set()
    custom_used = False
    for i, p in enumerate(body.items, start=1):
        if p.clause_id:
            c = db.scalar(select(HblClause).where(HblClause.id == p.clause_id, HblClause.organization_id == org))
            if not c or c.status != "ACTIVE":
                raise HTTPException(422, "Clause is not available (inactive or deleted)")
            if c.id in seen_ids or _norm(c.text) in seen_text:
                continue        # a clause never prints twice
            seen_ids.add(c.id)
            seen_text.add(_norm(c.text))
            snapshot.append({"clause_id": c.id, "code": c.code, "title": c.title, "text": c.text, "category": c.category, "group": c.exclusive_group or "", "mandatory": c.is_mandatory, "order": i, "source": "master"})
        else:
            text = (p.text or "").strip()
            if len(text) < 2:
                raise HTTPException(422, "A one-time clause needs its wording")
            if not has_permission(user, "hbl.clause.custom"):
                raise HTTPException(403, "You do not have the right to add a one-time clause to a House BL")
            if _norm(text) in seen_text:
                continue
            seen_text.add(_norm(text))
            custom_used = True
            snapshot.append({"clause_id": None, "code": "ONE-TIME", "title": (p.title or text[:60]).strip(), "text": text, "category": "Other", "group": "", "mandatory": False, "order": i, "source": "custom"})
    # mandatory clauses cannot be removed by an ordinary user
    removed = []
    for c in db.scalars(select(HblClause).where(HblClause.organization_id == org, HblClause.status == "ACTIVE", HblClause.is_mandatory.is_(True))):
        if applicable(c, job, bl) and c.id not in seen_ids and _norm(c.text) not in seen_text:
            removed.append(c)
    if removed:
        if not has_permission(user, "hbl.clause.override"):
            raise HTTPException(422, "Mandatory clause cannot be removed: " + ", ".join(f"{c.code} {c.title}" for c in removed))
        if len((body.override_reason or "").strip()) < 5:
            raise HTTPException(422, "Give the reason for removing a mandatory clause")
    msgs = [m for m in validate_selection(db, job, {**bl, "clausesSavedAt": "now"}, snapshot) if m.startswith("Conflicting")]
    if msgs:
        raise HTTPException(422, "; ".join(msgs))
    now = datetime.now(timezone.utc).isoformat()
    bl["clauses"] = snapshot
    bl["clausesSavedAt"] = now
    bl["clauseVersion"] = int(bl.get("clauseVersion") or 0) + 1
    hist = list(bl.get("clauseHistory") or [])
    hist.append({"version": bl["clauseVersion"], "at": now, "by": jl.display_name(user), "count": len(snapshot), "codes": [s["code"] for s in snapshot], "override": [c.code for c in removed], "reason": (body.override_reason or "").strip() if removed else "", "custom": custom_used})
    bl["clauseHistory"] = hist[-30:]
    row.bl = bl
    flag_modified(row, "bl")
    row.saved_sections = {**(row.saved_sections or {}), "clauses": now}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    jl.log_event(db, job, user, "HBL_CLAUSES_SAVED", "HEADER", f"HBL clauses saved (version {bl['clauseVersion']}, {len(snapshot)} clauses)", detail={"codes": [s["code"] for s in snapshot], "override": [c.code for c in removed]})
    record_audit(db, "HBL_CLAUSES_SAVED", "SHIPMENT", user, org, "Shipment", str(job.id), {"job_number": job.job_number, "version": bl["clauseVersion"], "count": len(snapshot), "override": [c.code for c in removed]})
    db.commit()
    return get_selection(job_number, user, db)
