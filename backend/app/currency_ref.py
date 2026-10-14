"""Currency Master rules: ISO 4217 reference, field validation, base-currency and protection rules, idempotent seed.

Stored in the existing master_records table (master_type='currency'); the attributes live in `data`:
currency_symbol, unicode_symbol, symbol_position (PREFIX|SUFFIX), iso_numeric_code (string), decimal_places, sub_currency_name,
sub_currency_ratio, default_currency (Yes|No = this is the organisation's BASE / home currency), system_default (Yes|No, system managed),
is_system_currency (yes|no, system managed), notes, exchange_rate (today's INR rate used by quotations; the dated exchange-rate history
is a separate module that does not exist yet). Sub-currency ratio is never an exchange rate.
"""
import csv
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Document, DocumentLine, JobCharge, MasterRecord, Organization, Quotation, QuotationLine

ISO_FILE = Path(__file__).resolve().parents[1] / "data" / "iso4217.csv"
PROTECTED_KEYS = ("is_system_currency", "system_default", "source")  # managed by the system: never taken from a client payload
UNICODE_RE = re.compile(r"^U\+[0-9A-Fa-f]{4,6}( U\+[0-9A-Fa-f]{4,6})*$")

# code -> (name, symbol, unicode, sub currency, ratio): the sample master supplied for the company; codes, numerics and decimals come from ISO 4217
SEED = [
    ("INR", "Indian Rupee", "₹", "U+20B9", "Paisa", 100), ("USD", "US Dollar", "$", "U+0024", "Cent", 100), ("EUR", "Euro", "€", "U+20AC", "Cent", 100),
    ("GBP", "British Pound Sterling", "£", "U+00A3", "Penny", 100), ("AED", "UAE Dirham", "د.إ", "U+062F U+002E U+0625", "Fils", 100), ("SAR", "Saudi Riyal", "ر.س", "U+0631 U+002E U+0633", "Halala", 100),
    ("SGD", "Singapore Dollar", "S$", "", "Cent", 100), ("AUD", "Australian Dollar", "A$", "", "Cent", 100), ("CAD", "Canadian Dollar", "C$", "", "Cent", 100),
    ("JPY", "Japanese Yen", "¥", "U+00A5", "Sen (historical)", 100), ("BDT", "Bangladeshi Taka", "৳", "U+09F3", "Poisha", 100), ("LKR", "Sri Lankan Rupee", "Rs.", "", "Cents", 100),
    ("CNY", "Chinese Yuan", "¥", "U+00A5", "Fen", 100), ("CHF", "Swiss Franc", "CHF", "", "Rappen", 100), ("MYR", "Malaysian Ringgit", "RM", "", "Sen", 100),
]


@lru_cache(maxsize=1)
def iso_table() -> dict:
    """Active ISO 4217 currencies: code -> {name, numeric (3-char string), minor (int or None)}."""
    out = {}
    with open(ISO_FILE, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["code"]] = {"name": r["name"], "numeric": r["numeric"], "minor": int(r["minor_unit"]) if r["minor_unit"].isdigit() else None}
    return out


def yes(value) -> bool:
    return str(value or "").strip().lower().startswith("yes")


def norm_position(value) -> str | None:
    v = str(value or "").strip().upper()
    if not v:
        return None
    if v.startswith("PREFIX"):
        return "PREFIX"
    if v.startswith("SUFFIX"):
        return "SUFFIX"
    raise HTTPException(422, "Symbol position must be Prefix or Suffix")


def referenced(db: Session, org_id: int, code: str) -> list[str]:
    """Where a currency is already used by business records (it can then never be deleted; deactivate it instead)."""
    where = []
    if db.scalar(select(func.count()).select_from(JobCharge).where(JobCharge.organization_id == org_id, or_(JobCharge.buy_ccy == code, JobCharge.sell_ccy == code))):
        where.append("job charges")
    if db.scalar(select(func.count()).select_from(QuotationLine).join(Quotation, Quotation.id == QuotationLine.quotation_id).where(QuotationLine.ccy == code, Quotation.organization_id == org_id)):
        where.append("quotations")
    if db.scalar(select(func.count()).select_from(DocumentLine).join(Document, Document.id == DocumentLine.document_id).where(DocumentLine.ccy == code, Document.organization_id == org_id)):
        where.append("invoices / documents")
    return where


def clean(db: Session, org_id: int, code: str, name: str, data: dict, existing: MasterRecord | None) -> dict:
    """Validate and normalise a currency record on the server. Returns the data to store."""
    code = code.strip().upper()
    ref = iso_table().get(code)
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise HTTPException(422, "Currency code must be 3 capital letters (ISO 4217)")
    if ref is None:
        raise HTTPException(422, f"{code} is not an ISO 4217 currency code")
    if not (name or "").strip():
        raise HTTPException(422, "Currency name is required")
    d = dict(data or {})
    # numeric code: a 3-character string (leading zeros kept) that must match the ISO code
    num = str(d.get("iso_numeric_code", "")).strip()
    if num == "":
        num = ref["numeric"]
    if not re.fullmatch(r"\d{3}", num):
        raise HTTPException(422, "ISO numeric code must be exactly 3 digits (e.g. 036)")
    if num != ref["numeric"]:
        raise HTTPException(422, f"ISO numeric code of {code} is {ref['numeric']}, not {num}")
    d["iso_numeric_code"] = num
    dec = str(d.get("decimal_places", "")).strip()
    if dec == "":
        dec = str(ref["minor"] if ref["minor"] is not None else 2)
    if not re.fullmatch(r"\d{1,2}", dec) or int(dec) > 4:
        raise HTTPException(422, "Decimal places must be a whole number from 0 to 4")
    if ref["minor"] is not None and int(dec) != ref["minor"]:
        raise HTTPException(422, f"{code} uses {ref['minor']} decimal places under ISO 4217 (you entered {dec})")
    d["decimal_places"] = str(int(dec))
    pos = norm_position(d.get("symbol_position")) or "PREFIX"
    d["symbol_position"] = pos
    uni = str(d.get("unicode_symbol", "")).strip().upper()
    if uni and not UNICODE_RE.fullmatch(uni):
        raise HTTPException(422, "Unicode symbol must be code points such as U+20B9 (several separated by spaces)")
    d["unicode_symbol"] = uni.upper()
    ratio = str(d.get("sub_currency_ratio", "")).strip()
    if ratio != "":
        try:
            r = Decimal(ratio)
        except InvalidOperation:
            raise HTTPException(422, "Sub currency ratio must be a number")
        if r <= 0:
            raise HTTPException(422, "Sub currency ratio must be greater than zero")
        d["sub_currency_ratio"] = ratio
    er = str(d.get("exchange_rate", "")).strip()
    if er != "":
        try:
            if Decimal(er.replace(",", "")) <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise HTTPException(422, "Exchange rate must be a positive number")
    # system-managed flags always come from the stored record, never from the client
    for k in PROTECTED_KEYS:
        if existing is not None and k in (existing.data or {}):
            d[k] = existing.data[k]
        else:
            d.pop(k, None)
    # base currency: only changed through the explicit "Change base currency" action
    was_base = existing is not None and yes((existing.data or {}).get("default_currency"))
    wants_base = yes(d.get("default_currency"))
    if wants_base and not was_base:
        raise HTTPException(409, "The base (home) currency is changed only with 'Set as base currency'. Do not set it in the form.")
    if was_base and not wants_base:
        raise HTTPException(409, "This is the organisation's base currency and cannot be switched off here. Set another currency as base instead.")
    d["default_currency"] = "Yes" if was_base else "No"
    d.setdefault("system_default", "No")
    return d


def check_status_change(existing: MasterRecord, new_status: str | None) -> None:
    if new_status == "INACTIVE" and yes((existing.data or {}).get("default_currency")):
        raise HTTPException(409, "The base currency cannot be deactivated")


def check_delete(db: Session, org_id: int, rec: MasterRecord) -> None:
    d = rec.data or {}
    if yes(d.get("is_system_currency")):
        raise HTTPException(409, "This is a system currency and cannot be deleted. You can deactivate it; history stays available")
    if yes(d.get("default_currency")):
        raise HTTPException(409, "The base currency cannot be deleted")
    where = referenced(db, org_id, rec.code)
    if where:
        raise HTTPException(409, f"{rec.code} is used in {', '.join(where)} and cannot be deleted. Deactivate it instead; inactive currencies stay available for reports")


def org_base(db: Session, org_id: int) -> MasterRecord | None:
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "currency")):
        if yes((r.data or {}).get("default_currency")):
            return r
    return None


def set_base(db: Session, org_id: int, rec: MasterRecord) -> MasterRecord | None:
    """Make `rec` the only base currency. Refused once posted documents exist (their amounts are in the old base currency)."""
    cur = org_base(db, org_id)
    if cur is not None and cur.id == rec.id:
        raise HTTPException(409, f"{rec.code} is already the base currency")
    if rec.status != "ACTIVE":
        raise HTTPException(409, "An inactive currency cannot become the base currency")
    if cur is not None and db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == org_id, Document.status == "POSTED")):
        raise HTTPException(409, "Posted invoices exist in the current base currency, so the base currency can no longer be changed")
    if cur is not None:
        cur.data = {**(cur.data or {}), "default_currency": "No"}
        db.flush()  # release the old base before the new one is written: the database allows only one
    rec.data = {**(rec.data or {}), "default_currency": "Yes"}
    db.flush()
    return cur


def seed(db: Session, org_id: int, user_id: int | None) -> dict:
    """Create the sample currencies that are missing. Existing records are never overwritten; differences from ISO 4217 are reported."""
    iso = iso_table()
    org = db.get(Organization, org_id)
    existing = {r.code: r for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "currency"))}
    have_base = any(yes((r.data or {}).get("default_currency")) for r in existing.values())
    have_sysdef = any(yes((r.data or {}).get("system_default")) for r in existing.values())
    created, skipped, conflicts = [], [], []
    inr_is_base = False
    for code, name, symbol, uni, sub, ratio in SEED:
        ref = iso[code]
        if code in existing:
            d = existing[code].data or {}
            skipped.append(code)
            for field, mine, theirs in (("iso_numeric_code", str(d.get("iso_numeric_code", "")).strip(), ref["numeric"]), ("decimal_places", str(d.get("decimal_places", "")).strip(), str(ref["minor"]))):
                if mine and mine != theirs:
                    conflicts.append({"code": code, "field": field, "existing": mine, "iso_4217": theirs})
            continue
        base = (code == "INR" and not have_base and (org.country or "").strip().lower() in ("india", "in"))
        sysdef = (code == "INR" and not have_sysdef)
        db.add(MasterRecord(organization_id=org_id, master_type="currency", code=code, name=name, status="ACTIVE", created_by=user_id, data={
            "currency_symbol": symbol, "unicode_symbol": uni, "symbol_position": "PREFIX", "iso_numeric_code": ref["numeric"], "decimal_places": str(ref["minor"]), "sub_currency_name": sub, "sub_currency_ratio": str(ratio),
            "default_currency": "Yes" if base else "No", "system_default": "Yes" if sysdef else "No", "is_system_currency": "yes", "source": "seed", "notes": ""}))
        created.append(code)
        inr_is_base = inr_is_base or (code == "INR" and base)
        have_base = have_base or base
        have_sysdef = have_sysdef or sysdef
    return {"created": created, "skipped": skipped, "conflicts": conflicts, "base_currency_set": "INR" if "INR" in created and inr_is_base else None}
