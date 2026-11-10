"""Output Docs of a job: the reports that apply to its mode and direction, generated only when the user asks, kept as numbered versions.

The server decides which reports apply (and which are restricted to people who may see financial figures), records every generation as a new version with who and when, and cleans the
document HTML before it is stored, because a stored document is shown to other users later (nothing but plain layout tags survives).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import job_lifecycle as jl
from .access import shipment_clause
from .auth import has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import JobOutputDoc, Shipment, User

router = APIRouter(tags=["output docs"])

# code, name, type, optional, variants, restricted (needs billing.read), only for LCL, operational entry needed
R = lambda code, name, typ, optional=False, variants=(), restricted=False, lcl=False, ops=False: {"code": code, "name": name, "type": typ, "optional": optional, "variants": list(variants), "restricted": restricted, "lcl": lcl, "ops": ops}
CATALOG = {
    ("Air", "Export"): [R("hawb", "HAWB / HAWBL", "Transport document"), R("mawb", "MAWB / MAWABL", "Transport document", optional=True), R("loading_confirmation", "Shipment Loading Confirmation", "Confirmation"),
                        R("pre_alert", "Pre-alert", "Notice"), R("stuffing_report", "Stuffing Report", "Operational", ops=True), R("transshipment_confirmation", "Transshipment Confirmation", "Confirmation"),
                        R("export_declaration", "Export Declaration", "Statutory"), R("air_cargo_manifest", "Air Cargo Manifest", "Manifest"), R("screening_letter", "Screening of Cargo Letter (to airline security)", "Letter"),
                        R("security_declaration", "Cargo Security Declaration", "Statutory")],
    ("Air", "Import"): [R("arrival_notice", "Arrival Notice", "Notice"), R("delivery_order", "Delivery Order Preparation", "Release document"), R("destuffing_report", "Destuffing Report (Unloading)", "Operational", ops=True),
                        R("pod", "Proof of Delivery (POD)", "Operational", ops=True), R("air_manifest", "Air Manifest", "Manifest")],
    ("Sea", "Export"): [R("hbl", "HBL (House Bill of Lading)", "Transport document"), R("mbl", "MBL (Master Bill of Lading)", "Transport document", optional=True), R("sailing_confirmation", "Sailing Confirmation", "Confirmation"),
                        R("shipment_summary", "Shipment Summary", "Summary", variants=("without_rates", "with_rates")), R("pl_report", "P&L Report", "Financial", restricted=True), R("pre_alert", "Pre-alert", "Notice"),
                        R("stuffing_report", "Stuffing Report (LCL Shipment)", "Operational", lcl=True, ops=True)],
    ("Sea", "Import"): [R("can", "Cargo Arrival Notice (CAN)", "Notice", variants=("with_rates", "without_rates")), R("delivery_order", "Delivery Order Preparation", "Release document"),
                        R("destuffing_report", "Destuffing Report (Unloading - LCL)", "Operational", lcl=True, ops=True), R("pod", "Proof of Delivery (POD)", "Operational", ops=True),
                        R("shipment_summary", "Shipment Summary", "Summary", variants=("without_rates", "with_rates"))],
}
VARIANT_LABEL = {"with_rates": "With Charges", "without_rates": "Without Charges", "": ""}
MAX_HTML = 600_000

_TAGS = {"div", "span", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "p", "br", "hr", "b", "i", "u", "strong", "em", "small", "sup", "sub", "ol", "ul", "li", "h1", "h2", "h3", "h4", "h5", "h6", "img", "colgroup", "col"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "form", "textarea", "select", "button", "link", "meta", "base", "title", "head"}
_ATTRS = {"style", "class", "colspan", "rowspan", "align", "valign", "width", "height", "alt", "title", "src", "span"}
_VOID = {"br", "hr", "img", "col"}
_BAD_STYLE = re.compile(r"expression|javascript:|vbscript:|@import|behavior|-moz-binding|url\s*\(\s*(?!['\"]?data:image/(png|jpe?g|gif|webp))", re.I)
_IMG = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in _TAGS:
            return
        keep = []
        for k, v in attrs:
            k = (k or "").lower()
            if k not in _ATTRS or v is None:
                continue
            if k == "style" and _BAD_STYLE.search(v):
                continue
            if k == "src" and not (tag == "img" and _IMG.match(v.strip())):
                continue
            keep.append(f' {k}="{v.replace(chr(34), "&quot;")}"')
        if tag == "img" and not any(a.startswith(" src=") for a in keep):
            return
        self.out.append(f"<{tag}{''.join(keep)}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in _TAGS and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data.replace("<", "&lt;").replace(">", "&gt;"))

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")


def clean_html(html: str) -> str:
    p = _Clean()
    p.feed(html)
    p.close()
    return "".join(p.out)


def _job(db: Session, user: User, job_number: str) -> Shipment:
    if user.organization_id is None:
        raise HTTPException(403, "Output Docs are tenant-scoped")
    q = select(Shipment).where(Shipment.organization_id == user.organization_id, Shipment.job_number == job_number)
    clause = shipment_clause(db, user)
    if clause is not None:
        q = q.where(clause)
    job = db.scalar(q)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _applicable(job: Shipment) -> list[dict]:
    rows = CATALOG.get((job.mode if job.mode != "Ocean" else "Sea", job.direction), [])
    lcl = (job.container_mode or "").upper() == "LCL"
    return [r for r in rows if not r["lcl"] or lcl]


def _may_see_finance(user: User) -> bool:
    return has_permission(user, "billing.read")


def _names(db: Session, org_id: int) -> dict:
    return {u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username for u in db.scalars(select(User).where(User.organization_id == org_id))}


def _meta(r: JobOutputDoc, names: dict, count: int) -> dict:
    return {"id": r.id, "version": r.version, "at": r.generated_at, "by": names.get(r.generated_by, "-"), "title": r.title, "count": count, "status": "Regenerated" if r.version > 1 else "Generated"}


@router.get("/api/jobs/{job_number:path}/outdocs/history")
def outdocs_history(job_number: str, code: str = Query(max_length=40), variant: str = Query(default="", max_length=20), user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    if not any(r["code"] == code for r in _applicable(job)):
        raise HTTPException(404, "That report does not apply to this job")
    names = _names(db, job.organization_id)
    rows = db.scalars(select(JobOutputDoc).where(JobOutputDoc.shipment_id == job.id, JobOutputDoc.code == code, JobOutputDoc.variant == variant).order_by(JobOutputDoc.version.desc()))
    return [{"id": r.id, "version": r.version, "at": r.generated_at, "by": names.get(r.generated_by, "-"), "title": r.title} for r in rows]


@router.get("/api/jobs/{job_number:path}/outdocs/{doc_id:int}")
def outdoc_get(job_number: str, doc_id: int, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    r = db.get(JobOutputDoc, doc_id)
    if not r or r.shipment_id != job.id:
        raise HTTPException(404, "Document not found")
    if (r.code == "pl_report" or r.variant == "with_rates") and not _may_see_finance(user):
        raise HTTPException(403, "This report shows financial figures: you do not have permission to open it")
    return {**_meta(r, _names(db, job.organization_id), 1), "code": r.code, "variant": r.variant, "html": r.html, "inputs": r.inputs or {}}


@router.get("/api/jobs/{job_number:path}/outdocs")
def outdocs_list(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """The reports that apply to this job and which of them have been generated. Nothing is generated by calling this."""
    job = _job(db, user, job_number)
    names = _names(db, job.organization_id)
    finance = _may_see_finance(user)
    rows = db.scalars(select(JobOutputDoc).where(JobOutputDoc.shipment_id == job.id).order_by(JobOutputDoc.version.desc())).all()
    latest: dict[tuple, JobOutputDoc] = {}
    counts: dict[tuple, int] = {}
    for r in rows:
        k = (r.code, r.variant)
        counts[k] = counts.get(k, 0) + 1
        latest.setdefault(k, r)
    out = []
    for rep in _applicable(job):
        gen = {}
        for k, r in latest.items():
            if k[0] == rep["code"]:
                gen[k[1]] = _meta(r, names, counts[k])
        out.append({**rep, "allowed": finance or not rep["restricted"], "generated": gen})
    return {"job_number": job.job_number, "mode": job.mode, "direction": job.direction, "container_mode": job.container_mode, "finance": finance, "reports": out, "variant_labels": VARIANT_LABEL}


class GenIn(BaseModel):
    code: str = Field(max_length=40)
    variant: str = Field(default="", max_length=20)
    title: str = Field(default="", max_length=200)
    html: str = Field(min_length=20)
    inputs: dict | None = None


@router.post("/api/jobs/{job_number:path}/outdocs", status_code=201)
def outdoc_generate(job_number: str, body: GenIn, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Record a report the user generated: a new version of that report (and variant) of the job. The browser builds the page from the job's latest saved data; this stores it with who and when."""
    job = _job(db, user, job_number)
    rep = next((r for r in _applicable(job) if r["code"] == body.code), None)
    if rep is None:
        raise HTTPException(422, "That report does not apply to this job")
    if rep["variants"] and body.variant not in rep["variants"]:
        raise HTTPException(422, "Choose which version of this report to generate: " + ", ".join(VARIANT_LABEL[v] for v in rep["variants"]))
    if not rep["variants"] and body.variant:
        raise HTTPException(422, "This report has no versions to choose from")
    if (rep["restricted"] or body.variant == "with_rates") and not _may_see_finance(user):
        raise HTTPException(403, "This report shows financial figures: you do not have permission to generate it")
    if job.status == "Cancelled":
        raise HTTPException(409, f"Job {job.job_number} was cancelled: its documents cannot be generated")
    html = clean_html(body.html)
    if len(html) < 20:
        raise HTTPException(422, "The report is empty")
    if len(html) > MAX_HTML:
        raise HTTPException(422, "The report is too large to store")
    last = db.scalar(select(func.max(JobOutputDoc.version)).where(JobOutputDoc.shipment_id == job.id, JobOutputDoc.code == body.code, JobOutputDoc.variant == body.variant)) or 0
    r = JobOutputDoc(organization_id=job.organization_id, shipment_id=job.id, code=body.code, variant=body.variant, version=last + 1, title=(body.title or rep["name"])[:200], html=html,
                     inputs=body.inputs or None, generated_by=user.id)
    db.add(r)
    db.flush()
    label = rep["name"] + (f" ({VARIANT_LABEL[body.variant]})" if body.variant else "")
    jl.log_event(db, job, user, "OUTDOC_GENERATED", "DOCUMENTS", f"{label} generated (version {r.version})", new=r.version)
    record_audit(db, "OUTDOC_GENERATED", "SHIPMENT", user, job.organization_id, "JobOutputDoc", str(r.id), {"job_number": job.job_number, "report": body.code, "variant": body.variant, "version": r.version})
    db.commit()
    return _meta(r, _names(db, job.organization_id), r.version)
