"""Job screens: Parties, Cargo, Containers, Air dimensions and BL / AWB are saved per section, and what is saved feeds the job, the lists and the documents."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import job_lifecycle as jl
from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from .models import HblCounter, JobDetail, Organization, Shipment, User

router = APIRouter(prefix="/api/jobs", tags=["job details"])

SECTIONS = ("parties", "cargo", "containers", "dims", "bl")
HBL_TYPES = {"OTL": True, "FMC": True, "MTO": False}   # type -> needs a SCAC code
CONTAINER_NO = re.compile(r"^[A-Z]{4}[0-9]{7}$")
SCAC = re.compile(r"^[A-Z]{4}$")


class SectionIn(BaseModel):
    data: Any
    expected_version: int | None = None


class HblIn(BaseModel):
    hbl_type: str = Field(pattern="^(OTL|FMC|MTO)$")
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


def _out(db: Session, job: Shipment, row: JobDetail | None) -> dict:
    by = db.get(User, row.updated_by) if row and row.updated_by else None
    return {"job_number": job.job_number, "mode": job.mode, "direction": job.direction, "pol": job.pol, "pod": job.pod, "pol_code": location_code(job.pol), "pod_code": location_code(job.pod),
            "company_code": company_code(db.get(Organization, job.organization_id)),
            "parties": (row.parties if row else {}) or {}, "cargo": (row.cargo if row else {}) or {}, "containers": (row.containers if row else []) or [], "dims": (row.dims if row else {}) or {}, "bl": (row.bl if row else {}) or {},
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
        return data
    if not isinstance(data, dict):
        raise HTTPException(422, f"The {section} section must be an object")
    if len(str(data)) > 40000:
        raise HTTPException(422, "Too much data in this section")
    if section == "cargo":
        for k in ("pkgs", "gwt", "nwt", "vol"):
            if data.get(k) not in (None, "") and _num(data.get(k)) is None:
                raise HTTPException(422, f"{ {'pkgs': 'No. of packages', 'gwt': 'Gross weight', 'nwt': 'Net weight', 'vol': 'Volume'}[k] } must be a positive number")
    if section == "bl":
        if data.get("hblType") and data["hblType"] not in HBL_TYPES:
            raise HTTPException(422, "House BL type must be OTL, FMC or MTO")
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
    return _out(db, job, _row(db, job))


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
        keep = {k: v for k, v in (row.bl or {}).items() if k in ("hblNo", "hblDate", "hblIssuedFor") and not data.get(k)}
        data = {**data, **keep}   # the House BL number belongs to the system: a save never erases or replaces it
        if (row.bl or {}).get("hblNo"):
            data["hblType"] = (row.bl or {}).get("hblType") or data.get("hblType")
    first = not (row.saved_sections or {}).get(section)
    setattr(row, section, data)
    row.saved_sections = {**(row.saved_sections or {}), section: datetime.now(timezone.utc).isoformat()}
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    _reflect(job, row)
    jl.log_event(db, job, user, "JOB_DETAILS_SAVED" if first else "JOB_DETAILS_UPDATED", "HEADER", f"{section.capitalize()} {'saved' if first else 'updated'}", detail={"section": section, "version": row.version})
    record_audit(db, "JOB_DETAILS_SAVED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "section": section})
    db.commit()
    return _out(db, job, row)


@router.post("/{job_number}/hbl")
def issue_hbl(job_number: str, body: HblIn, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Issue the House BL number: company code + type + running number + POL code + POD code (e.g. RTSMTO001MAASIN). A job gets one; asking again returns it."""
    job = _job(db, user, job_number, lock=True)
    jl.assert_job_editable(job, "the House BL")
    if job.mode != "Sea":
        raise HTTPException(422, "A House BL is issued for sea jobs; air jobs carry a House AWB")
    row = _row(db, job, create=True)
    bl = dict(row.bl or {})
    if bl.get("hblNo"):
        return {**_out(db, job, row), "existing": True}
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
    return {**_out(db, job, row), "existing": False}


@router.get("/{job_number}/hbl-preview")
def hbl_next(job_number: str, hbl_type: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """What the next House BL number of this type would look like for this job (not issued)."""
    if hbl_type not in HBL_TYPES:
        raise HTTPException(422, "House BL type must be OTL, FMC or MTO")
    job = _job(db, user, job_number)
    ctr = db.scalar(select(HblCounter).where(HblCounter.organization_id == job.organization_id, HblCounter.hbl_type == hbl_type))
    return {"preview": hbl_preview(db, job, hbl_type, (ctr.last_no if ctr else 0) + 1), "pol_code": location_code(job.pol), "pod_code": location_code(job.pod)}
