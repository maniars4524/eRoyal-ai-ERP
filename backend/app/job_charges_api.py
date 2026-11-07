from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import cast, delete, select
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from . import basis_calc as bc
from . import charge_rules
from . import job_lifecycle as jl
from .models import Booking, Customer, JobCharge, JobDetail, MasterRecord, Shipment, User

router = APIRouter(prefix="/api/shipments", tags=["job-charges"])

TWO = Decimal("0.01")
MAX_LINES = 200
TAX_TYPES = "^(Taxable|Non-Taxable|Pure Agent|RCM)$"


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


class Side(BaseModel):
    ccy: str = Field(default="INR", pattern="^[A-Z]{3}$")
    exch: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=6)
    rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    tax_type: str = Field(default="Taxable", pattern=TAX_TYPES)
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=2)
    party: str = Field(default="", max_length=160)
    use_party: bool = False

    @field_validator("exch")
    @classmethod
    def _exch(cls, value: Decimal) -> Decimal:
        return value


class Breakup(BaseModel):
    type: str = Field(default="", max_length=40)
    qty: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=3)
    sell: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    buy: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)


class ChargeLine(BaseModel):
    charge_name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=240)
    uom: str = Field(default="Per Shipment", max_length=40)
    qty: Decimal = Field(default=Decimal("1"), ge=0, max_digits=14, decimal_places=3)
    hsn: str = Field(default="", max_length=20)
    buy: Side = Field(default_factory=Side)
    sell: Side = Field(default_factory=Side)
    breakup: list[Breakup] | None = Field(default=None, max_length=30)
    dr_cr: str = Field(default="DR", pattern="^(DR|CR)$")        # DR = amount added, CR = amount deducted
    qty_manual: bool = False            # the user typed the quantity: a calculated Basis does not overwrite it
    pct_base: list[str] | None = Field(default=None, max_length=60)      # "% of Other Charges": the charges the percentage is taken from


class ChargesPayload(BaseModel):
    lines: list[ChargeLine] = Field(default_factory=list, max_length=MAX_LINES)


def compute_side(qty: Decimal, ccy: str, exch: Decimal, rate: Decimal, tax_type: str, tax_pct: Decimal) -> dict:
    amount = qty * rate
    inr = amount if ccy == "INR" else amount * exch
    tax = inr * tax_pct / Decimal(100) if tax_type == "Taxable" else Decimal(0)
    return {"amount": q2(amount), "inr": q2(inr), "tax": q2(tax)}


def line_out(row: JobCharge) -> dict:
    buy = compute_side(row.qty, row.buy_ccy, row.buy_exch, row.buy_rate, row.buy_tax_type, row.buy_tax_pct)
    sell = compute_side(row.qty, row.sell_ccy, row.sell_exch, row.sell_rate, row.sell_tax_type, row.sell_tax_pct)
    return {
        "id": row.id, "line_no": row.line_no, "dr_cr": row.dr_cr or "DR", "breakup": row.breakup or [], "charge_name": row.charge_name, "description": row.description, "uom": row.uom, "qty": str(row.qty), "hsn": row.hsn, "qty_manual": bool(row.qty_manual), "pct_base": row.pct_base,
        "buy": {"pct": None if row.buy_pct is None else str(row.buy_pct), "ccy": row.buy_ccy, "exch": str(row.buy_exch), "rate": str(row.buy_rate), "tax_type": row.buy_tax_type, "tax_pct": str(row.buy_tax_pct), "party": row.vendor, "use_party": row.use_vendor, **{k: str(v) for k, v in buy.items()}},
        "sell": {"pct": None if row.sell_pct is None else str(row.sell_pct), "ccy": row.sell_ccy, "exch": str(row.sell_exch), "rate": str(row.sell_rate), "tax_type": row.sell_tax_type, "tax_pct": str(row.sell_tax_pct), "party": row.client, "use_party": row.use_client, **{k: str(v) for k, v in sell.items()}},
        "_buy": buy, "_sell": sell,
        "_dc": {"ccy": row.sell_ccy if (row.sell_rate or 0) > 0 else row.buy_ccy, "amount": sell["amount"] if (row.sell_rate or 0) > 0 else buy["amount"]},
    }


def dr_cr_statement(lines: list[dict]) -> list[dict]:
    """Per currency: total DR (added), total CR (deducted), the net balance and what it means. The amount of a line is its sell amount when it has a sell rate, otherwise its buy amount; tax is not included.
    Currencies are never mixed: each has its own balance. Net > 0 is a Debit Note (DN) to the overseas agent, net < 0 a Credit Note (CN), zero needs neither."""
    by: dict[str, dict] = {}
    for l in lines:
        d = l["_dc"]
        if not d["amount"]:
            continue
        e = by.setdefault(d["ccy"], {"ccy": d["ccy"], "dr": Decimal(0), "cr": Decimal(0)})
        e["dr" if l.get("dr_cr", "DR") == "DR" else "cr"] += d["amount"]
    out = []
    for e in by.values():
        net = e["dr"] - e["cr"]
        out.append({"ccy": e["ccy"], "dr": str(q2(e["dr"])), "cr": str(q2(e["cr"])), "net": str(q2(net)), "note": "DN" if net > 0 else "CN" if net < 0 else "NONE"})
    return sorted(out, key=lambda x: x["ccy"])


def totals(lines: list[dict]) -> dict:
    ap = sum((l["_buy"]["inr"] for l in lines), Decimal(0))
    ar = sum((l["_sell"]["inr"] for l in lines), Decimal(0))
    ap_tax = sum((l["_buy"]["tax"] for l in lines), Decimal(0))
    ar_tax = sum((l["_sell"]["tax"] for l in lines), Decimal(0))
    gp = ar - ap
    pct = q2(gp / ar * Decimal(100)) if ar else Decimal(0)
    return {"dr_cr": dr_cr_statement(lines), "ar": str(ar), "ar_tax": str(ar_tax), "ar_total": str(ar + ar_tax), "ap": str(ap), "ap_tax": str(ap_tax), "ap_total": str(ap + ap_tax), "gross_profit": str(gp), "gp_percent": str(pct)}


def _job(db: Session, user: User, job_number: str) -> Shipment:
    if user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Charges are tenant-scoped; Super Admin does not manage job charges")
    job = db.scalar(select(Shipment).where(Shipment.organization_id == user.organization_id, Shipment.job_number == job_number))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _response(rows: list[JobCharge]) -> dict:
    lines = [line_out(r) for r in rows]
    clean = [{k: v for k, v in l.items() if not k.startswith("_")} for l in lines]
    return {"lines": clean, "totals": totals(lines)}


class DrCrNoteIn(BaseModel):
    ccy: str = Field(pattern="^[A-Z]{3}$")
    party: str | None = Field(default=None, max_length=200)


@router.post("/by-number/{job_number:path}/dr-cr-note", status_code=201)
def raise_dr_cr_note(job_number: str, body: DrCrNoteIn, user: User = Depends(permission_required("billing.create")), db: Session = Depends(get_db)):
    """The net balance of the DR / CR statement in one currency becomes a draft Debit Note (net DR) or Credit Note (net CR) to the overseas agent. A balanced currency needs neither, and the same balance is not raised twice."""
    from .documents_api import DocumentIn, LineIn as DocLine, _new_document, _load, _out, proforma_required
    from .models import Document

    job = _job(db, user, job_number)
    rows = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id).order_by(JobCharge.line_no)))
    st = next((x for x in dr_cr_statement([line_out(r) for r in rows]) if x["ccy"] == body.ccy), None)
    if not st or st["note"] == "NONE":
        raise HTTPException(422, f"The DR / CR statement is balanced in {body.ccy}: no debit note or credit note is required")
    doc_type = "debit_note" if st["note"] == "DN" else "credit_note"
    net = abs(Decimal(st["net"]))
    prior = [d for d in db.scalars(select(Document).where(Document.organization_id == job.organization_id, Document.job_number == job.job_number, Document.doc_type.in_(("debit_note", "credit_note")), Document.status != "CANCELLED"))
             if (d.header or {}).get("dr_cr_ccy") == body.ccy]
    if prior:
        raise HTTPException(409, f"A {prior[0].doc_type.replace('_', ' ')} for the {body.ccy} balance of this job has already been raised ({prior[0].doc_number or 'draft'}). Cancel it before raising another")
    det = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    pa = (det.parties if det else None) or {}
    party = (body.party or "").strip() or ((pa.get("deliveryAgent") or pa.get("originAgent")) if job.direction != "Import" else (pa.get("originAgent") or pa.get("deliveryAgent"))) or ""
    if not party:
        raise HTTPException(422, "Choose the overseas agent: set the Delivery Agent / Origin Agent in the job's Parties tab, or name the party")
    exch = Decimal(1)
    if body.ccy != "INR":
        exch = next((x for r in rows for x in ((r.sell_exch if r.sell_ccy == body.ccy else None), (r.buy_exch if r.buy_ccy == body.ccy else None)) if x), Decimal(1))
    line = DocLine(charge_name="Net balance as per DR / CR statement", description=f"DR {st['dr']} less CR {st['cr']} ({body.ccy})", qty=Decimal(1), ccy=body.ccy, exch=exch, rate=net, tax_type="Non-Taxable", tax_pct=Decimal(0), job_number=job.job_number)
    doc = _new_document(db, user, job.organization_id, DocumentIn(doc_type=doc_type, job_number=job.job_number, party_name=party, lines=[line], header={"dr_cr_ccy": body.ccy, "dr_cr_net": st["net"]},
                                                                  remarks=f"Net balance of the DR / CR statement of job {job.job_number}"))
    record_audit(db, "DR_CR_NOTE_RAISED", "BILLING", user, job.organization_id, doc.doc_type, str(doc.id), {"job_number": job.job_number, "ccy": body.ccy, "net": st["net"], "party": party})
    db.commit()
    out = _out(_load(db, job.organization_id, doc.id), True, proforma_required(db, job.organization_id))
    return {"id": doc.id, "doc_type": doc.doc_type, "party": party, "ccy": body.ccy, "amount": str(net), "document": out}


@router.get("/by-number/{job_number:path}/charges")
def get_charges(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    rows = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id).order_by(JobCharge.line_no)))
    return _response(rows)


@router.get("/by-number/{job_number:path}/charge-parties")
def charge_parties(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """Who can be named on the charges of a job: the Client of a sell charge is one of the job's own parties (its customer and the parties of the Parties & Cargo tab or the booking); the Vendor of a buy charge is a party
    of the Organization Master in a vendor category (carrier, agent, CHA, transporter, AP)."""
    from .job_details_api import booking_defaults
    from .schemas_auth import VENDOR_ROLES

    job = _job(db, user, job_number)
    clients: list[str] = []
    cust = db.get(Customer, job.customer_id) if job.customer_id else None
    if cust:
        clients.append(cust.name)
    row = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    parties = (row.parties if row and (row.saved_sections or {}).get("parties") else booking_defaults(db, job)["parties"]) or {}
    for k in ("shipper", "consignee", "notify1", "notify2", "billTo", "agent", "overseasAgent"):
        v = str(parties.get(k) or "").strip()
        if v and not v.startswith("__") and v.lower() != "same as consignee" and v not in clients:
            clients.append(v)
    vendors = [c.name for c in db.scalars(select(Customer).where(Customer.organization_id == job.organization_id, Customer.status == "ACTIVE", cast(Customer.roles, JSONB).has_any(array(VENDOR_ROLES))).order_by(Customer.name))]
    return {"clients": clients, "vendors": vendors}


def job_measures(db: Session, job: Shipment, det: JobDetail | None) -> bc.Measures:
    """The shipment's quantities as the job knows them: what was saved on the job, else what the booking carries."""
    from .job_details_api import booking_defaults

    cargo = {**(booking_defaults(db, job).get("cargo") or {}), **{k: v for k, v in ((det.cargo if det else None) or {}).items() if v not in ("", None)}}
    bk = db.get(Booking, job.booking_id) if job.booking_id else None
    from . import cargo_engine as ce

    rows = []
    dims_doc = (det.dims if det else None) or {}
    unit = dims_doc.get("unit")
    row_gross, dim_packages = None, None
    if ce.is_v2(dims_doc):
        try:
            rows, kg = ce.measures_from_dims(dims_doc)
        except ce.CargoError as e:
            raise bc.BasisError(str(e))
        shipment_kg = ce.dec(dims_doc.get("shipment_gross_kg"), "Gross weight")
        dim_packages = sum((r["packages"] for r in rows), ce.ZERO) or None
        row_gross = kg if kg > 0 else (shipment_kg if shipment_kg > 0 else None)       # weights typed on the dimension rows win, then the weight saved with them, then the cargo tab's (never added together)
    else:
        for r in (dims_doc.get("rows") or []):
            f = 2.54 if unit == "in_lbs" else 1
            rows.append({"l": float(bc.dec(r.get("l"))) * f, "w": float(bc.dec(r.get("w"))) * f, "h": float(bc.dec(r.get("h"))) * f, "packages": r.get("pkg") or 1})
    return bc.Measures(mode=job.mode or "Sea", shipment_type=(getattr(job, "container_mode", None) or (bk.shipment_type if bk else "") or ""), packages=cargo.get("pkgs") or dim_packages, gross_kg=row_gross if row_gross is not None else cargo.get("gwt"), net_kg=cargo.get("nwt"),
                       volume_cbm=cargo.get("vol"), vehicles=cargo.get("vehicles"), dims=rows)


def basis_amounts(db: Session, job: Shipment, det: JobDetail | None, lines: list) -> dict:
    """Index of the payload line -> {qty} for a measure-based Basis, {pct: {BUY/SELL: {pct, amount}}} for a percentage Basis. Refuses what cannot be calculated (422)."""
    wanted = [(i, l) for i, l in enumerate(lines) if l.uom in bc.BASES and not (l.breakup or [])]
    if not wanted:
        return {}
    try:
        m = job_measures(db, job, det)
    except bc.BasisError as e:
        raise HTTPException(422, f"Shipment measurements: {e}")
    cats = {r.name.strip().lower(): str((r.data or {}).get("category") or "") for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == job.organization_id, MasterRecord.master_type == "charge"))}
    calc_in, out = [], {}
    for i, l in enumerate(lines):
        cat = cats.get(l.charge_name.strip().lower(), "")
        for side, key in ((l.buy, "BUY"), (l.sell, "SELL")):
            calc_in.append({"id": f"{i}:{key}", "side": key, "basis": l.uom if not (l.breakup or []) else "", "rate": str(side.rate), "qty": str(l.qty), "name": l.charge_name, "category": cat, "ccy": side.ccy, "exch": str(side.exch if side.ccy != "INR" else 1), "include": l.pct_base if l.uom == bc.PCT_OTHER else None})
    res = bc.calc_lines(calc_in, m)
    byid = {x["id"]: x for x in res["lines"]}
    for i, l in wanted:
        entry: dict = {}
        priced = l.buy.rate > 0 or l.sell.rate > 0
        for key, side in (("BUY", l.buy), ("SELL", l.sell)):
            x = byid[f"{i}:{key}"]
            if l.uom in bc.PERCENT_BASES:
                if side.rate <= 0:
                    continue
                if x["error"]:
                    raise HTTPException(422, f"{l.charge_name or 'Charge'} ({l.uom}): {x['error']}")
                entry.setdefault("pct", {})[key] = {"pct": side.rate, "amount": Decimal(x["amount"])}
            elif "qty" not in entry and not l.qty_manual:
                if x["error"]:
                    if priced:
                        raise HTTPException(422, f"{l.charge_name or 'Charge'} ({l.uom}): {x['error']}")
                    continue
                entry["qty"] = Decimal(x["quantity"])
        out[i] = entry
    return out


class PreviewLine(BaseModel):
    id: int | str = 0
    side: str = "SELL"
    basis: str = ""
    rate: Decimal = Decimal(0)
    qty: Decimal = Decimal(1)
    name: str = ""
    ccy: str = "INR"
    exch: Decimal = Decimal(1)
    include: list[str] | None = None


class PreviewPayload(BaseModel):
    lines: list[PreviewLine] = Field(default_factory=list, max_length=300)


@router.post("/by-number/{job_number:path}/basis-calc")
def basis_preview(job_number: str, payload: PreviewPayload, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    """What the charges save would calculate (quantities and percentage amounts) from the job's own measurements: the screen shows it while the user types. Nothing is saved."""
    job = _job(db, user, job_number)
    det = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    try:
        m = job_measures(db, job, det)
    except bc.BasisError as e:
        raise HTTPException(422, f"Shipment measurements: {e}")
    cats = {r.name.strip().lower(): str((r.data or {}).get("category") or "") for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == job.organization_id, MasterRecord.master_type == "charge"))}
    lines = [{"id": str(l.id), "side": l.side, "basis": l.basis, "rate": str(l.rate), "qty": str(l.qty), "name": l.name, "category": cats.get(l.name.strip().lower(), ""), "ccy": l.ccy, "exch": str(l.exch if l.ccy != "INR" else 1), "include": l.include if l.basis == bc.PCT_OTHER else None} for l in payload.lines]
    return bc.calc_lines(lines, m)


@router.put("/by-number/{job_number:path}/charges")
def replace_charges(job_number: str, payload: ChargesPayload, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Replace all charge lines of a job atomically; the server recalculates every amount."""
    job = _job(db, user, job_number)
    jl.assert_job_editable(job, "its charges")
    if (job.cha_info or {}).get("accounts_moved"):
        raise HTTPException(status_code=409, detail="The charges of this job are with Accounts and cannot be changed here; take them back from Accounts first")
    before = jl.money_totals(db, job)
    charge_rules.check_sides(db, job.organization_id, [(l.charge_name, "SELL", l.sell.rate > 0) for l in payload.lines] + [(l.charge_name, "BUY", l.buy.rate > 0) for l in payload.lines])
    for line in payload.lines:
        for side in (line.buy, line.sell):
            if side.ccy != "INR" and side.exch <= 0:
                raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR charges")
    old_basis = {(r.charge_name, r.uom, bool(r.qty_manual)) for r in db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id))}
    db.execute(delete(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id))
    from . import air_weight

    det = db.scalar(select(JobDetail).where(JobDetail.shipment_id == job.id))
    chargeable_kg = air_weight.compute((det.dims if det else None) or {}).get("chargeable_kg") or 0          # from the package dimensions on the job
    calc = basis_amounts(db, job, det, payload.lines)          # Basis quantities and percentage amounts, from the one calculation service
    rows = []
    for index, line in enumerate(payload.lines, start=1):
        bu = [{"type": b.type.strip(), "qty": str(b.qty), "sell": str(b.sell), "buy": str(b.buy)} for b in (line.breakup or []) if b.qty > 0]
        if bu:      # a container-wise charge is one head: quantity 1, the total of the break-up as the rate of each side that has one
            sell_total, buy_total = sum((b.qty * b.sell for b in line.breakup if b.qty > 0), Decimal(0)), sum((b.qty * b.buy for b in line.breakup if b.qty > 0), Decimal(0))
            if sell_total > 0:
                line.sell.rate = sell_total
            if buy_total > 0:
                line.buy.rate = buy_total
            line.qty = Decimal(1)
        if chargeable_kg > 0 and air_weight.is_chargeable_uom(line.uom) and not bu:        # a charge on a chargeable-weight basis is quoted on the weight calculated from the dimensions
            line.qty = Decimal(str(chargeable_kg))
        cl = calc.get(index - 1) or {}
        if cl.get("qty") is not None and not bu:
            line.qty = cl["qty"]
        if cl.get("pct"):                                          # a percentage charge is stored as one unit at the calculated amount, with the percentage kept beside it
            line.qty = Decimal(1)
            for side, key in ((line.buy, "BUY"), (line.sell, "SELL")):
                if key in cl["pct"]:
                    side.rate = cl["pct"][key]["amount"]
        row = JobCharge(
            organization_id=job.organization_id, shipment_id=job.id, line_no=index, charge_name=line.charge_name.strip(), description=line.description.strip(), uom=line.uom, qty=line.qty, hsn=line.hsn.strip(),
            buy_ccy=line.buy.ccy, buy_exch=Decimal(1) if line.buy.ccy == "INR" else line.buy.exch, buy_rate=line.buy.rate, buy_tax_type=line.buy.tax_type, buy_tax_pct=line.buy.tax_pct if line.buy.tax_type in ("Taxable", "RCM") else Decimal(0), vendor=line.buy.party.strip(), use_vendor=line.buy.use_party,
            sell_ccy=line.sell.ccy, sell_exch=Decimal(1) if line.sell.ccy == "INR" else line.sell.exch, sell_rate=line.sell.rate, sell_tax_type=line.sell.tax_type, sell_tax_pct=line.sell.tax_pct if line.sell.tax_type in ("Taxable", "RCM") else Decimal(0), client=line.sell.party.strip(), use_client=line.sell.use_party,
        )
        row.breakup = bu or None
        row.dr_cr = line.dr_cr
        row.qty_manual = bool(line.qty_manual and line.uom in bc.BASES)
        row.pct_base = [str(n)[:160] for n in line.pct_base] if line.uom == bc.PCT_OTHER and line.pct_base is not None else None
        if cl.get("pct"):
            row.buy_pct = cl["pct"]["BUY"]["pct"] if "BUY" in cl["pct"] else None
            row.sell_pct = cl["pct"]["SELL"]["pct"] if "SELL" in cl["pct"] else None
        db.add(row)
        rows.append(row)
    db.flush()
    result = _response(rows)
    new_basis = {(r.charge_name, r.uom, bool(r.qty_manual)) for r in rows}
    changed = sorted(f"{n}: {u}{' (quantity typed)' if m else ''}" for n, u, m in new_basis - old_basis if u in bc.BASES)
    if changed:
        record_audit(db, "JOB_CHARGE_BASIS_CHANGED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "lines": changed[:20]})
    record_audit(db, "JOB_CHARGES_SAVED", "SHIPMENT", user, job.organization_id, "Shipment", str(job.id), {"job_number": job.job_number, "lines": len(rows), **{k: result["totals"][k] for k in ("ar", "ap", "gross_profit")}})
    job.revenue = Decimal(result["totals"]["ar"])
    after = jl.money_totals(db, job)
    corr = __import__("uuid").uuid4().hex[:16]
    if (before["sell"], before["sell_lines"]) != (after["sell"], after["sell_lines"]):
        jl.log_event(db, job, user, "SELL_COST_CHANGED", "SELLCOST", f"Sell cost changed: {before['sell']} to {after['sell']} ({after['sell_lines']} charge lines)", previous=before["sell"], new=after["sell"], correlation_id=corr, related_type="JOB_CHARGES", detail={"lines": after["sell_lines"]})
    if (before["buy"], before["buy_lines"]) != (after["buy"], after["buy_lines"]):
        jl.log_event(db, job, user, "BUY_COST_CHANGED", "BUYCOST", f"Buy cost changed: {before['buy']} to {after['buy']} ({after['buy_lines']} charge lines)", previous=before["buy"], new=after["buy"], correlation_id=corr, related_type="JOB_CHARGES", detail={"lines": after["buy_lines"]})
    if before["margin"] != after["margin"]:
        jl.log_event(db, job, user, "JOB_MARGIN_CHANGED", "FINANCE", f"Job margin recalculated: {before['margin']} to {after['margin']}", previous=before["margin"], new=after["margin"], correlation_id=corr)
    jl.recompute_stage(db, job, "CHARGES_UPDATED", user, corr)
    db.commit()
    saved = db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id).order_by(JobCharge.line_no))
    return _response(list(saved))
