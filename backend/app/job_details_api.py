"""Job screens: Parties, Cargo, Containers, Air dimensions and BL / AWB are saved per section, and what is saved feeds the job, the lists and the documents."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import billing, job_lifecycle as jl
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import Booking, CompanyDocProfile, Document, HblCounter, JobDetail, Organization, Quotation, Shipment, User

router = APIRouter(prefix="/api/jobs", tags=["job details"])

SECTIONS = ("parties", "cargo", "containers", "dims", "bl", "impdocs")
HBL_TYPES = {"OTL": True, "FMC": True, "MTO": False, "HAWB": False}   # type -> needs a SCAC code (HAWB is the House Air Waybill of an air job)
CONTAINER_NO = re.compile(r"^[A-Z]{4}[0-9]{7}$")
SCAC = re.compile(r"^[A-Z]{4}$")


class SectionIn(BaseModel):
    data: Any
    expected_version: int | None = None


class HblIn(BaseModel):
    hbl_type: str = Field(pattern="^(OTL|FMC|MTO|HAWB)$")
    scac: str | None = Field(default=None, max_length=4)


def _tenant(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Job details are tenant-scoped")
    return user.organization_id


def _job(db: Session, user: User, job_number: str, lock: bool = False) -> Shipment:
    q = select(Shipment).where(Shipment.organization_id == _tenant(user), Shipment.job_number == job_number)
    job = db.scalar(q.with_for_update() if lock else q)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _row(db: Session, job: Shipment, create: bool = False) -> JobDetail | None:
    row = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    if row is None and create:
        row = JobDetail(organization_id=job.organization_id, shipment_id=job.id, parties={}, cargo={}, containers=[], dims={}, bl={}, saved_sections={}, version=0)
        db.add(row)
        db.flush()
    return row


def location_code(port: str | None) -> str:
    """The 3-letter location of a port: INMAA(CHENNAI) -> MAA, SGSIN -> SIN, MAA(CHENNAI INTERNATIONAL AIRPORT) -> MAA."""
    t = re.split(r"[\(\-\s]", (port or "").strip(), maxsplit=1)[0].upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    if len(t) == 5 and t[:2].isalpha():   # a UN/LOCODE: 2-letter country + 3-letter location
        return t[2:5]
    return t[:3] if len(t) >= 3 else "XXX"


def company_code(org: Organization) -> str:
    return (re.sub(r"[^A-Z0-9]", "", (org.code or "").upper())[:3] or "ORG")


def hbl_preview(db: Session, job: Shipment, hbl_type: str, number: int | None = None) -> str:
    org = db.get(Organization, job.organization_id)
    return f"{company_code(org)}{hbl_type}{(number if number is not None else 0):03d}{location_code(job.pol)}{location_code(job.pod)}"


BL_STAGES = ("Draft", "Confirmed", "Released")
RELEASE_TYPES = ("Original", "Express Release", "Telex Release", "Surrender")
PROFILE_TEXT = ("hbl_scac_codes", "tagline", "address", "phone", "mobile", "fax", "email", "web", "reg_otl", "reg_mto", "reg_fmc", "signatory", "signatory_name", "terms", "legal_name", "gstin", "pan", "cin", "state", "state_code",
                "bank_name", "bank_account", "bank_ifsc", "bank_swift", "bank_branch", "payment_modes", "tracking_url", "invoice_terms", "do_terms", "eoe", "footer_note", "usd_bank_name", "usd_bank_account", "usd_bank_swift", "usd_bank_address", "usd_bank_intermediary")
PROFILE_IMAGES = ("sign_image", "seal_image")
IMG_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")


def release_check(db: Session, job: Shipment, user: User | None = None) -> dict:
    """Is the job's money settled? An Original BL is released only after Accounts has confirmed full payment of every posted invoice / debit note of the job."""
    docs = [d for d in db.scalars(select(Document).where(Document.organization_id == job.organization_id, Document.job_number == job.job_number, Document.doc_type.in_(("invoice", "debit_note")), Document.status == "POSTED"))]
    owing = sum((billing.outstanding(d) for d in docs), start=0)
    ok = bool(docs) and owing <= 0
    why = "Full payment confirmed" if ok else ("No posted invoice for this job yet" if not docs else f"Outstanding payment of {owing:,.2f}")
    return {"payment_ok": ok, "outstanding": str(owing), "invoices": len(docs), "message": why, "can_override": bool(user and has_permission(user, "bl.release_override"))}


def booking_defaults(db: Session, job: Shipment) -> dict:
    """What the booking already knows (parties, cargo, container), used until the user saves that tab on the job."""
    b = db.get(Booking, job.booking_id) if job.booking_id else None
    if not b:
        return {"parties": {}, "cargo": {}, "containers": []}
    d = b.data or {}
    consignee = d.get("consignee") or ""
    notify = d.get("notify") or ""
    if notify.strip().lower() == "same as consignee":
        notify = consignee
    parties = {"shipper": d.get("shipper") or b.party_name or "", "consignee": consignee, "notify1": notify, "billTo": b.party_name or ""}
    cargo = {"commodity": d.get("commodity") or d.get("marks") or "", "pkgs": d.get("pcs") or "", "pkgType": d.get("pack_type") or "", "gwt": d.get("gwt") or "", "nwt": d.get("nwt") or "", "vol": d.get("volume") or "", "marks": d.get("marks") or "", "cargoDesc": d.get("cargo_description") or ""}
    containers = []
    if d.get("container_no"):
        containers = [{"container_no": str(d["container_no"]).upper().replace(" ", ""), "type": d.get("container_type") or ""}]
    return {"parties": {k: v for k, v in parties.items() if v}, "cargo": {k: v for k, v in cargo.items() if v not in ("", None)}, "containers": containers}


def suggested_containers(db: Session, job: Shipment) -> list[dict]:
    """The containers the booking and the quotation already planned (type and number of boxes), offered on the job's Containers tab so they are not typed again."""
    found: dict[str, dict] = {}
    b = db.get(Booking, job.booking_id) if job.booking_id else None
    if b:
        d = b.data or {}
        t = str(d.get("container_type") or "").strip()
        if t:
            try:
                qty = int(float(str(d.get("container_qty") or 1)))
            except ValueError:
                qty = 1
            found[t] = {"type": t, "qty": max(qty, 1), "sources": ["Booking " + (b.number or "draft")]}
    quotes = []
    if b and b.quotation_id:
        q = db.get(Quotation, b.quotation_id)
        if q:
            quotes.append(q)
    quotes += [q for q in db.scalars(select(Quotation).where(Quotation.organization_id == job.organization_id, Quotation.job_number == job.job_number)) if q not in quotes]
    for q in quotes:
        per: dict[str, int] = {}
        for ln in q.lines:
            for bu in (ln.container_breakup or []):
                t = str(bu.get("type") or "").strip()
                try:
                    n = int(float(str(bu.get("qty") or 0)))
                except ValueError:
                    n = 0
                if t and n > 0:
                    per[t] = max(per.get(t, 0), n)       # sell and buy lines repeat the same boxes: take the larger, never add them up
        for t, n in per.items():
            src = "Quotation " + (q.number or "draft")
            if t in found:
                found[t]["sources"].append(src)
            else:
                found[t] = {"type": t, "qty": n, "sources": [src]}
    return list(found.values())


def _out(db: Session, job: Shipment, row: JobDetail | None, user: User | None = None) -> dict:
    saved = (row.saved_sections if row else {}) or {}
    pre = booking_defaults(db, job)
    by = db.get(User, row.updated_by) if row and row.updated_by else None
    return {"job_number": job.job_number, "mode": job.mode, "direction": job.direction, "pol": job.pol, "pod": job.pod, "pol_code": location_code(job.pol), "pod_code": location_code(job.pod),
            "company_code": company_code(db.get(Organization, job.organization_id)),
            "parties": ((row.parties if row else {}) or {}) if saved.get("parties") else pre["parties"], "cargo": ((row.cargo if row else {}) or {}) if saved.get("cargo") else pre["cargo"], "containers": ((row.containers if row else []) or []) if saved.get("containers") else pre["containers"],
            "release": release_check(db, job, user), "suggested_containers": suggested_containers(db, job),
            "from_booking": {"parties": not saved.get("parties") and bool(pre["parties"]), "cargo": not saved.get("cargo") and bool(pre["cargo"])}, "dims": (row.dims if row else {}) or {}, "bl": (row.bl if row else {}) or {}, "impdocs": (row.impdocs if row else {}) or {},
            "saved_sections": (row.saved_sections if row else {}) or {}, "version": row.version if row else 0, "updated_at": row.updated_at if row and row.version else None,
            "updated_by": jl.display_name(by) if by else None}


def _num(v: Any) -> float | None:
    try:
        n = float(str(v).replace(",", "").strip())
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _validate(section: str, data: Any) -> Any:
    if section == "containers":
        if not isinstance(data, list) or len(data) > 200:
            raise HTTPException(422, "Containers must be a list of at most 200 rows")
        seen = set()
        for i, r in enumerate(data, start=1):
            if not isinstance(r, dict):
                raise HTTPException(422, f"Container row {i} is not valid")
            no = str(r.get("container_no") or "").strip().upper().replace(" ", "").replace("-", "")
            r["container_no"] = no
            if no and not CONTAINER_NO.match(no):
                raise HTTPException(422, f"Container {i}: '{no}' is not a valid container number (4 letters and 7 digits, e.g. MSCU1234567)")
            if no and no in seen:
                raise HTTPException(422, f"Container {no} is entered twice")
            seen.add(no)
            if r.get("vgm") not in (None, "") and _num(r.get("vgm")) is None:
                raise HTTPException(422, f"Container {i}: VGM must be a number")
            for k, label in (("gross_wt", "Gross weight"), ("net_wt", "Net weight")):
                if r.get(k) not in (None, "") and _num(r.get(k)) is None:
                    raise HTTPException(422, f"Container {i}: {label} must be a positive number")
            if r.get("pkgs") not in (None, ""):
                n = _num(r.get("pkgs"))
                if n is None or n != int(n):
                    raise HTTPException(422, f"Container {i}: packages must be a whole number")
            if _num(r.get("gross_wt")) is not None and _num(r.get("net_wt")) is not None and _num(r["net_wt"]) > _num(r["gross_wt"]):
                raise HTTPException(422, f"Container {i}: net weight cannot be more than the gross weight")
        return data
    if not isinstance(data, dict):
        raise HTTPException(422, f"The {section} section must be an object")
    if len(str(data)) > 40000:
        raise HTTPException(422, "Too much data in this section")
    if section == "cargo":
        if _num(data.get("nwt")) is not None and _num(data.get("gwt")) is not None and _num(data["nwt"]) > _num(data["gwt"]):
            raise HTTPException(422, "Net weight cannot be more than the gross weight")
        for k in ("pkgs", "gwt", "nwt", "vol"):
            if data.get(k) not in (None, "") and _num(data.get(k)) is None:
                raise HTTPException(422, f"{ {'pkgs': 'No. of packages', 'gwt': 'Gross weight', 'nwt': 'Net weight', 'vol': 'Volume'}[k] } must be a positive number")
    if section == "bl":
        if data.get("hblType") and data["hblType"] not in HBL_TYPES:
            raise HTTPException(422, "House BL type must be OTL, FMC or MTO (HAWB for air)")
        if data.get("scac") and not SCAC.match(str(data["scac"]).upper()):
            raise HTTPException(422, "SCAC code is 4 capital letters")
    return data


def _reflect(job: Shipment, row: JobDetail) -> None:
    """What the user saved on the screens becomes the job's own data, so the lists, the overview and the documents agree."""
    c = row.cargo or {}
    if c.get("commodity"):
        job.commodity = str(c["commodity"])[:160]
    if _num(c.get("pkgs")) is not None and float(c["pkgs"]) == int(float(c["pkgs"])):
        job.packages = int(float(c["pkgs"]))
    if _num(c.get("gwt")) is not None:
        job.gross_weight = _num(c["gwt"])
    if _num(c.get("vol")) is not None:
        job.volume = _num(c["vol"])
    if c.get("pkgType"):
        job.package_type = str(c["pkgType"])[:40]
    p = row.parties or {}
    if p.get("consignee") and not str(p["consignee"]).startswith("__"):
        job.consignee = str(p["consignee"])[:160]
    b = row.bl or {}
    ref = (b.get("hblNo") if b.get("bookingType") == "House" and b.get("hblNo") else None) or (b.get("directNo") if b.get("bookingType") == "Direct" else None) or b.get("mblNo") or b.get("directNo")
    if ref:
        job.bl_awb = str(ref)[:60]


@router.get("/{job_number}/details")
def get_details(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    return _out(db, job, _row(db, job), user)


@router.get("/{job_number}/links")
def job_links(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Everything connected to this shipment: its booking, the quotation behind it, the other jobs of the same booking, and its invoices / bills / notes. Used for the links on the job page."""
    job = _job(db, user, job_number)
    b = db.get(Booking, job.booking_id) if job.booking_id else None
    quotes = []
    if b and b.quotation_id:
        q = db.get(Quotation, b.quotation_id)
        if q:
            quotes.append(q)
    quotes += [q for q in db.scalars(select(Quotation).where(Quotation.organization_id == job.organization_id, Quotation.job_number == job.job_number)) if q not in quotes]
    related = []
    if job.booking_id:
        related = [{"job_number": x.job_number, "job_type": x.job_type, "job_stage": x.job_stage} for x in db.scalars(select(Shipment).where(Shipment.organization_id == job.organization_id, Shipment.booking_id == job.booking_id, Shipment.id != job.id).order_by(Shipment.id))]
    docs = [{"id": d.id, "doc_type": d.doc_type, "doc_number": d.doc_number, "status": d.status, "party": d.party_name, "total": str(d.grand_total), "outstanding": str(billing.outstanding(d)) if d.status == "POSTED" else "0"}
            for d in db.scalars(select(Document).where(Document.organization_id == job.organization_id, Document.job_number == job.job_number).order_by(Document.id))]
    return {"job_number": job.job_number, "booking": {"id": b.id, "number": b.number, "status": b.status} if b else None, "quotations": [{"id": q.id, "number": q.number, "status": q.status} for q in quotes], "related_jobs": related, "documents": docs}


@router.put("/{job_number}/details/{section}")
def save_section(job_number: str, section: str, body: SectionIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    if section not in SECTIONS:
        raise HTTPException(404, "Unknown section")
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the job details")
    data = _validate(section, body.data)
    row = _row(db, job, create=True)
    if body.expected_version is not None and body.expected_version != row.version:
        raise HTTPException(409, "These details were changed by someone else. Reload the job and try again")
    if section == "bl":
        keep = {k: v for k, v in (row.bl or {}).items() if k in ("hblNo", "hblDate", "hblIssuedFor", "blStage", "releaseType", "digitalSign", "releasedAt", "releaseOverride") and not data.get(k)}
        data = {**data, **keep}   # the House BL number belongs to the system: a save never erases or replaces it
        if (row.bl or {}).get("hblNo"):
            data["hblType"] = (row.bl or {}).get("hblType") or data.get("hblType")
        for k in ("blStage", "releaseType", "digitalSign", "releasedAt", "releaseOverride"):
            if k in (row.bl or {}):
                data[k] = row.bl[k]
    first = not (row.saved_sections or {}).get(section)
    setattr(row, section, data)
    row.saved_sections = {**(row.saved_sections or {}), section: datetime.now(timezone.utc).isoformat()}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    _reflect(job, row)
    jl.log_event(db, job, user, "JOB_DETAILS_SAVED" if first else "JOB_DETAILS_UPDATED", "HEADER", f"{section.capitalize()} {'saved' if first else 'updated'}", detail={"section": section, "version": row.version})
    record_audit(db, "JOB_DETAILS_SAVED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "section": section})
    db.commit()
    return _out(db, job, row, user)


@router.post("/{job_number}/hbl")
def issue_hbl(job_number: str, body: HblIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Issue the House BL number: company code + type + running number + POL code + POD code (e.g. RTSMTO001MAASIN). A job gets one; asking again returns it."""
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the House BL")
    if job.mode == "Air" and body.hbl_type != "HAWB":
        raise HTTPException(422, "An air job carries a House AWB (HAWB)")
    if job.mode != "Air" and body.hbl_type == "HAWB":
        raise HTTPException(422, "The House AWB is for air jobs; a sea job carries a House BL (OTL, FMC or MTO)")
    if job.mode not in ("Sea", "Air"):
        raise HTTPException(422, "A House BL is issued for sea jobs and a House AWB for air jobs")
    row = _row(db, job, create=True)
    bl = dict(row.bl or {})
    if bl.get("hblNo"):
        return {**_out(db, job, row, user), "existing": True}
    if HBL_TYPES[body.hbl_type]:
        scac = (body.scac or "").upper()
        if not SCAC.match(scac):
            raise HTTPException(422, f"{body.hbl_type} House BL needs the carrier's 4-letter SCAC code")
        bl["scac"] = scac
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == job.organization_id, HblCounter.hbl_type == body.hbl_type).with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=job.organization_id, hbl_type=body.hbl_type, last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    number = hbl_preview(db, job, body.hbl_type, ctr.last_no)
    bl.update({"bookingType": "House", "hblType": body.hbl_type, "hblNo": number, "hblDate": date.today().isoformat()})
    row.bl = bl
    row.saved_sections = {**(row.saved_sections or {}), "bl": datetime.now(timezone.utc).isoformat()}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    _reflect(job, row)
    jl.log_event(db, job, user, "HBL_ISSUED", "DOCUMENTS", f"House BL {number} issued", new=number, detail={"hbl_type": body.hbl_type})
    record_audit(db, "HBL_ISSUED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "hbl": number})
    db.commit()
    return {**_out(db, job, row, user), "existing": False}


@router.get("/{job_number}/hbl-preview")
def hbl_next(job_number: str, hbl_type: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """What the next House BL number of this type would look like for this job (not issued)."""
    if hbl_type not in HBL_TYPES:
        raise HTTPException(422, "House BL type must be OTL, FMC or MTO (HAWB for air)")
    job = _job(db, user, job_number)
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == job.organization_id, HblCounter.hbl_type == hbl_type))
    return {"preview": hbl_preview(db, job, hbl_type, (ctr.last_no if ctr else 0) + 1), "pol_code": location_code(job.pol), "pod_code": location_code(job.pod)}


class BlStageIn(BaseModel):
    stage: str = Field(pattern="^(Draft|Confirmed|Released)$")
    release_type: str | None = None
    digital_sign: bool = False
    override: bool = False
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/{job_number}/bl/stage")
def bl_stage(job_number: str, body: BlStageIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Draft -> Confirmed -> Released. An Original BL is released only after full payment; a user with the release-override right may release it earlier, with a reason that goes to the job history."""
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the BL")
    row = _row(db, job, create=True)
    bl = dict(row.bl or {})
    cur = bl.get("blStage") or "Draft"
    number = (bl.get("hblNo") if bl.get("bookingType", "House") == "House" else bl.get("directNo")) or None
    if body.stage == cur:
        raise HTTPException(409, f"The BL is already {cur}")
    if cur == "Released":
        raise HTTPException(409, "A released BL cannot be moved back")
    if body.stage == "Released" and cur != "Confirmed":
        raise HTTPException(409, "Confirm the BL before releasing it")
    if body.stage in ("Confirmed", "Released") and not number:
        raise HTTPException(422, "Generate the House BL number (or enter the carrier BL number) before confirming the BL")
    event, text, detail = "BL_CONFIRMED", "BL confirmed", {}
    if body.stage == "Draft":
        event, text = "BL_RETURNED_TO_DRAFT", "BL returned to Draft"
    if body.stage == "Released":
        rtype = body.release_type or ""
        if rtype not in RELEASE_TYPES:
            raise HTTPException(422, "Choose the release type: Original, Express Release, Telex Release or Surrender")
        if body.digital_sign:
            prof = db.scalar(select(CompanyDocProfile).where(CompanyDocProfile.organization_id == job.organization_id))
            pd = (prof.data if prof else {}) or {}
            if not (pd.get("sign_image") or pd.get("seal_image")):
                raise HTTPException(422, "Digital signature and seal need the company's signature or seal image. Add it under Company documents profile first")
        rel = release_check(db, job, user)
        event, text = "BL_RELEASED", f"BL released ({rtype})"
        detail = {"release_type": rtype, "digital_sign": body.digital_sign, "payment_ok": rel["payment_ok"]}
        if rtype == "Original" and not rel["payment_ok"]:
            if not body.override:
                raise HTTPException(409, {"message": f"Original BL is released only after full payment is confirmed by Accounts. {rel['message']}.", "can_override": rel["can_override"], "code": "PAYMENT_PENDING"})
            if not rel["can_override"]:
                raise HTTPException(403, "You do not have the right to release an Original BL before payment is confirmed")
            if len((body.reason or "").strip()) < 5:
                raise HTTPException(422, "Give the reason for releasing the Original BL before payment is confirmed")
            event, text = "BL_RELEASE_OVERRIDDEN", f"Original BL released before payment confirmation ({rel['message']})"
            detail.update({"override": True, "outstanding": rel["outstanding"]})
            bl["releaseOverride"] = {"by": jl.display_name(user), "reason": body.reason.strip(), "at": datetime.now(timezone.utc).isoformat(), "outstanding": rel["outstanding"]}
        bl.update({"releaseType": rtype, "digitalSign": body.digital_sign, "releasedAt": datetime.now(timezone.utc).isoformat()})
        if not bl.get("released"):
            bl["released"] = date.today().isoformat()
    bl["blStage"] = body.stage
    row.bl = bl
    row.saved_sections = {**(row.saved_sections or {}), "bl": datetime.now(timezone.utc).isoformat()}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    jl.log_event(db, job, user, event, "DOCUMENTS", f"{text}: {number}", previous=cur, new=body.stage, reason=(body.reason or None), detail=detail)
    record_audit(db, event, "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "bl": number, **detail})
    db.commit()
    return _out(db, job, row, user)


# ---------------------------------------------------------------- company document profile (letterhead, registration numbers, signature and seal)
def _profile(db: Session, org_id: int) -> dict:
    p = db.scalar(select(CompanyDocProfile).where(CompanyDocProfile.organization_id == org_id))
    return dict((p.data if p else {}) or {})


def print_profile(db: Session, org_id: int) -> dict:
    """The profile as printed: the Bank Master's default bank (else the first active one) supplies the bank block, a foreign-currency account supplies the international one."""
    from .models import MasterRecord

    prof = _profile(db, org_id)
    banks = [r for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "bank", MasterRecord.status == "ACTIVE").order_by(MasterRecord.id))]
    inr = [b for b in banks if str((b.data or {}).get("currency") or "INR").upper() == "INR"]
    main = next((b for b in inr if str((b.data or {}).get("default_bank", "")).lower() == "yes"), inr[0] if inr else None)
    if main:
        d = main.data or {}
        prof.update({k: v for k, v in {"bank_name": main.name, "bank_account": d.get("account_number"), "bank_ifsc": d.get("ifsc"), "bank_swift": d.get("swift"), "bank_beneficiary": d.get("beneficiary"), "upi_id": d.get("upi_id"),
                                       "bank_branch": ", ".join(x for x in (d.get("branch"), d.get("branch_address")) if x)}.items() if v})
    fx = [b for b in banks if str((b.data or {}).get("currency") or "INR").upper() != "INR"]
    if fx:
        d = fx[0].data or {}
        prof.update({k: v for k, v in {"usd_bank_name": fx[0].name, "usd_bank_account": d.get("account_number"), "usd_bank_swift": d.get("swift"), "usd_bank_address": d.get("branch_address") or d.get("branch")}.items() if v})
    return prof


company = APIRouter(prefix="/api/company", tags=["company documents"])


@company.get("/doc-profile")
def get_profile(user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    org = db.get(Organization, _tenant(user))
    prof = _profile(db, org.id)
    return {"profile": prof, "hbl_scac": [c for c in str(prof.get("hbl_scac_codes") or "").split() if c], "print_profile": print_profile(db, org.id), "name": org.legal_name or org.name, "logo": org.logo, "company_code": company_code(org)}


@company.put("/doc-profile")
def put_profile(body: dict, user: User = Depends(permission_required("master.update")), db: Session = Depends(get_db)):
    import base64

    org = db.get(Organization, _tenant(user))
    p = db.scalar(select(CompanyDocProfile).where(CompanyDocProfile.organization_id == org.id))
    data = dict((p.data if p else {}) or {})
    for k in PROFILE_TEXT:
        if k in body:
            v = str(body[k] or "").strip()
            if len(v) > 4000:
                raise HTTPException(422, f"{k} is too long")
            if k == "hbl_scac_codes":
                codes = []
                for c in re.split(r"[\s,;]+", v.upper()):
                    if not c:
                        continue
                    if not SCAC.match(c):
                        raise HTTPException(422, f"HBL SCAC code {c} is not valid: a SCAC code is 4 letters")
                    if c not in codes:
                        codes.append(c)
                v = "\n".join(codes)
            data[k] = v
    for k in PROFILE_IMAGES:
        if k in body:
            v = body[k]
            if not v:
                data.pop(k, None)
                continue
            m = IMG_RE.match(str(v))
            if not m:
                raise HTTPException(422, "Signature and seal must be PNG, JPEG or WebP images")
            import base64 as b64

            try:
                raw = b64.b64decode(m.group(2), validate=True)
            except Exception:
                raise HTTPException(422, "The image is damaged")
            if len(raw) > 300_000:
                raise HTTPException(422, "The image is larger than 300 KB")
            data[k] = v
    if p is None:
        p = CompanyDocProfile(organization_id=org.id, data=data)
        db.add(p)
    p.data = data
    p.updated_by = user.id
    record_audit(db, "COMPANY_DOC_PROFILE_SAVED", "ORGANIZATION", user, org.id, "Organization", str(org.id), {"fields": sorted(k for k in body if k in PROFILE_TEXT + PROFILE_IMAGES)})
    db.commit()
    return {"profile": data}


class SendDocIn(BaseModel):
    to: str = Field(max_length=500)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=4000)
    filename: str = Field(default="document.pdf", max_length=120)
    pdf_base64: str = Field(max_length=8_000_000)


@router.post("/{job_number}/send-document")
def send_document(job_number: str, body: SendDocIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Email a job document (the PDF made in the browser) to the customer. Needs SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM in the server settings."""
    import base64
    import os
    import smtplib
    from email.message import EmailMessage

    job = _job(db, user, job_number)
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise HTTPException(503, "Email is not set up on this server yet. Ask the administrator to add the SMTP mail settings; meanwhile download the PDF and send it from your own mail")
    to = [a.strip() for a in re.split(r"[;,]", body.to) if a.strip()]
    if not to or len(to) > 10 or not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", a) for a in to):
        raise HTTPException(422, "Enter valid email addresses (up to 10, separated by commas)")
    try:
        raw = base64.b64decode(body.pdf_base64, validate=True)
    except Exception:
        raise HTTPException(422, "The attachment is damaged")
    if not raw.startswith(b"%PDF") or len(raw) > 5_000_000:
        raise HTTPException(422, "The attachment must be a PDF under 5 MB")
    org = db.get(Organization, job.organization_id)
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "noreply@localhost"
    msg["To"] = ", ".join(to)
    msg["Subject"] = body.subject
    msg["Reply-To"] = user.email
    msg.set_content((body.message or "Please find the attached document.") + f"\n\n{org.legal_name or org.name}")
    msg.add_attachment(raw, maintype="application", subtype="pdf", filename=re.sub(r"[^A-Za-z0-9._ -]", "_", body.filename))
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
            if os.getenv("SMTP_TLS", "true").lower() not in ("0", "false", "no"):
                s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)
    except Exception as e:
        raise HTTPException(502, f"The mail server did not accept the message: {str(e)[:160]}")
    jl.log_event(db, job, user, "DOCUMENT_EMAILED", "DOCUMENTS", f"{body.filename} emailed to {', '.join(to)}", detail={"to": to, "subject": body.subject})
    db.commit()
    return {"sent": True, "to": to}


@router.post("/{job_number}/do-issue")
def issue_do(job_number: str, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """The Delivery Order number: a running number per company, given once to a job."""
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the Delivery Order")
    if job.direction != "Import":
        raise HTTPException(422, "A Delivery Order is issued for import jobs")
    row = _row(db, job, create=True)
    d = dict(row.impdocs or {})
    if d.get("do_no"):
        return {**_out(db, job, row, user), "existing": True}
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == job.organization_id, HblCounter.hbl_type == "DO").with_for_update())
    if ctr is None:
        ctr = HblCounter(organization_id=job.organization_id, hbl_type="DO", last_no=0)
        db.add(ctr)
        db.flush()
    ctr.last_no += 1
    d.update({"do_no": str(ctr.last_no), "do_date": date.today().isoformat()})
    row.impdocs = d
    row.saved_sections = {**(row.saved_sections or {}), "impdocs": datetime.now(timezone.utc).isoformat()}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    jl.log_event(db, job, user, "DO_ISSUED", "DOCUMENTS", f"Delivery Order {d['do_no']} issued", new=d["do_no"])
    db.commit()
    return {**_out(db, job, row, user), "existing": False}


@company.get("/party-text")
def party_text_api(name: str, user: User = Depends(permission_required("master.read")), db: Session = Depends(get_db)):
    from .jobs_lifecycle_api import party_text
    from .models import Customer, OrgBranch

    org_id = _tenant(user)
    c = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == name))
    b = db.scalar(select(OrgBranch).where(OrgBranch.customer_id == c.id, OrgBranch.address.is_not(None), OrgBranch.address != "").order_by(OrgBranch.sl_no, OrgBranch.id)) if c else None
    return {"text": party_text(db, org_id, name), "gstin": (b.gstin if b and b.gstin else (c.gstin if c else None)) or "", "phone": (b.phone if b else (c.data or {}).get("phone") if c else "") or ""}
