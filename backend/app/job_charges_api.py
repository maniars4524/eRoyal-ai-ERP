from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import cast, delete, select
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session

from .auth import permission_required
from .auth_api import record_audit
from .db import get_db
from . import charge_rules
from . import job_lifecycle as jl
from .models import Customer, JobCharge, JobDetail, Shipment, User

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
        "id": row.id, "line_no": row.line_no, "breakup": row.breakup or [], "charge_name": row.charge_name, "description": row.description, "uom": row.uom, "qty": str(row.qty), "hsn": row.hsn,
        "buy": {"ccy": row.buy_ccy, "exch": str(row.buy_exch), "rate": str(row.buy_rate), "tax_type": row.buy_tax_type, "tax_pct": str(row.buy_tax_pct), "party": row.vendor, "use_party": row.use_vendor, **{k: str(v) for k, v in buy.items()}},
        "sell": {"ccy": row.sell_ccy, "exch": str(row.sell_exch), "rate": str(row.sell_rate), "tax_type": row.sell_tax_type, "tax_pct": str(row.sell_tax_pct), "party": row.client, "use_party": row.use_client, **{k: str(v) for k, v in sell.items()}},
        "_buy": buy, "_sell": sell,
    }


def totals(lines: list[dict]) -> dict:
    ap = sum((l["_buy"]["inr"] for l in lines), Decimal(0))
    ar = sum((l["_sell"]["inr"] for l in lines), Decimal(0))
    ap_tax = sum((l["_buy"]["tax"] for l in lines), Decimal(0))
    ar_tax = sum((l["_sell"]["tax"] for l in lines), Decimal(0))
    gp = ar - ap
    pct = q2(gp / ar * Decimal(100)) if ar else Decimal(0)
    return {"ar": str(ar), "ar_tax": str(ar_tax), "ar_total": str(ar + ar_tax), "ap": str(ap), "ap_tax": str(ap_tax), "ap_total": str(ap + ap_tax), "gross_profit": str(gp), "gp_percent": str(pct)}


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


@router.get("/by-number/{job_number}/charges")
def get_charges(job_number: str, user: User = Depends(permission_required("shipment.read")), db: Session = Depends(get_db)):
    job = _job(db, user, job_number)
    rows = list(db.scalars(select(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id).order_by(JobCharge.line_no)))
    return _response(rows)


@router.get("/by-number/{job_number}/charge-parties")
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


@router.put("/by-number/{job_number}/charges")
def replace_charges(job_number: str, payload: ChargesPayload, user: User = Depends(permission_required("shipment.update")), db: Session = Depends(get_db)):
    """Replace all charge lines of a job atomically; the server recalculates every amount."""
    job = _job(db, user, job_number)
    jl.assert_job_editable(job, "its charges")
    before = jl.money_totals(db, job)
    charge_rules.check_sides(db, job.organization_id, [(l.charge_name, "SELL", l.sell.rate > 0) for l in payload.lines] + [(l.charge_name, "BUY", l.buy.rate > 0) for l in payload.lines])
    for line in payload.lines:
        for side in (line.buy, line.sell):
            if side.ccy != "INR" and side.exch <= 0:
                raise HTTPException(status_code=422, detail="Exchange rate must be greater than zero for non-INR charges")
    db.execute(delete(JobCharge).where(JobCharge.shipment_id == job.id, JobCharge.organization_id == job.organization_id))
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
        row = JobCharge(
            organization_id=job.organization_id, shipment_id=job.id, line_no=index, charge_name=line.charge_name.strip(), description=line.description.strip(), uom=line.uom, qty=line.qty, hsn=line.hsn.strip(),
            buy_ccy=line.buy.ccy, buy_exch=Decimal(1) if line.buy.ccy == "INR" else line.buy.exch, buy_rate=line.buy.rate, buy_tax_type=line.buy.tax_type, buy_tax_pct=line.buy.tax_pct if line.buy.tax_type in ("Taxable", "RCM") else Decimal(0), vendor=line.buy.party.strip(), use_vendor=line.buy.use_party,
            sell_ccy=line.sell.ccy, sell_exch=Decimal(1) if line.sell.ccy == "INR" else line.sell.exch, sell_rate=line.sell.rate, sell_tax_type=line.sell.tax_type, sell_tax_pct=line.sell.tax_pct if line.sell.tax_type in ("Taxable", "RCM") else Decimal(0), client=line.sell.party.strip(), use_client=line.sell.use_party,
        )
        row.breakup = bu or None
        db.add(row)
        rows.append(row)
    db.flush()
    result = _response(rows)
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
