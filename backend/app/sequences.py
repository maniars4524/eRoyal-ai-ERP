"""Central sequence service: format building, validation, scope resolution and atomic, duplicate-proof number issuing.

Every centrally numbered record (jobs, quotations, bookings, invoices, vouchers, CRM records ...) gets its number from `issue()`.
* A number is built from the active SequenceConfig that matches the request (document type + branch + financial year + shipment type).
  Most specific match wins; two equally specific matches are a configuration conflict and the request is refused.
* The counter row is locked FOR UPDATE inside the caller's transaction, so concurrent requests can never share a number and a rolled-back
  transaction never leaves a half-issued number behind. Every issued number is also written to a unique registry (final safeguard).
* Nothing is ever truncated: a number that would exceed its length limit (or its numeric width) is refused as "exhausted".
* Where a company has not configured anything yet, a system-default sequence that reproduces the legacy format is created and continues from
  the legacy counter, so existing numbering keeps working and no issued number is repeated.
"""
import re
import zlib
from datetime import date, datetime, timezone
from itertools import product

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Booking, DocSequence, Document, IssuedNumber, MasterRecord, Payment, Quotation, SequenceConfig, SequenceCounter, SequenceEvent, Shipment, User

FINANCIAL_MAX = 16  # invoices and vouchers: the whole identifier, separators included
DEFAULT_MAX = 40  # every business table stores identifiers in VARCHAR(40)
HARD_MAX = 40
ALLOWED_SEPARATORS = "/-._"
CODE_RE = re.compile(r"^[A-Z0-9]{1,12}$")
FINAL_RE = re.compile(r"^[A-Za-z0-9/._-]+$")
RESET_POLICIES = ("NEVER", "FY", "CALENDAR")
FY_FORMATS = {"YY-YY": "26-27", "YYYY-YY": "2026-27", "FYYY": "FY26", "YYYY": "2026", "YY": "26"}
COMPONENT_TYPES = ("prefix", "suffix", "custom", "sep", "branch", "shipment", "doctype", "fy", "cyear", "cyear2", "number")
SHIPMENT_KEYS = ["Sea Export", "Sea Import", "Air Export", "Air Import", "Cross-Trade", "Consolidation", "Road Domestic",
                 "Sea Export FCL", "Sea Export LCL", "Sea Import FCL", "Sea Import LCL"]
DEFAULT_SHIPMENT_CODES = {"Sea Export": "SE", "Sea Import": "SI", "Air Export": "AE", "Air Import": "AI", "Cross-Trade": "CT", "Consolidation": "CN", "Road Domestic": "RD"}
LEGACY_MODE = {"Sea": "SEA", "Air": "AIR", "Road": "ROD", "Rail": "RAI"}
LEGACY_DIR = {"Export": "EXP", "Import": "IMP", "Domestic": "DOM", "Cross-Trade": "XTR"}

# doc_type -> (module, label, doc code, financial?, shipment aware?, integrated into a screen/API today?, legacy seq_type, default width, default reset)
REGISTRY = {
    "job": ("Jobs & Shipments", "Job Number", "JOB", False, True, True, "job", 6, "CALENDAR"),
    "master_job": ("Jobs & Shipments", "Master Job Reference", "MJ", False, True, False, None, 5, "FY"),
    "house_job": ("Jobs & Shipments", "House Job Reference", "HJ", False, True, False, None, 5, "FY"),
    "lead": ("Sales & CRM", "Lead Number", "LEAD", False, False, True, None, 5, "FY"),
    "enquiry": ("Sales & CRM", "Enquiry Number", "ENQ", False, False, True, None, 5, "FY"),
    "quotation": ("Sales & CRM", "Quotation Number", "QT", False, False, True, "quotation", 5, "FY"),
    "opportunity": ("Sales & CRM", "Sales Opportunity Number", "OPP", False, False, True, None, 5, "FY"),
    "followup": ("Sales & CRM", "Follow-up Reference", "FUP", False, False, True, None, 5, "FY"),
    "crm_booking": ("Sales & CRM", "Customer Booking Number", "CBK", False, False, True, None, 5, "FY"),
    "booking": ("Shipping & Documentation", "Booking Number", "BKG", False, False, True, "booking", 5, "FY"),
    "shipping_instruction": ("Shipping & Documentation", "Shipping Instruction Reference", "SI", False, True, False, None, 5, "FY"),
    "hbl": ("Shipping & Documentation", "House Bill of Lading Reference", "HBL", False, True, False, None, 5, "FY"),
    "hawb": ("Shipping & Documentation", "House Air Waybill Reference", "HAWB", False, True, False, None, 5, "FY"),
    "delivery_order": ("Shipping & Documentation", "Delivery Order Reference", "DO", False, True, False, None, 5, "FY"),
    "cargo_receipt": ("Shipping & Documentation", "Cargo Receipt Reference", "CR", False, True, False, None, 5, "FY"),
    "warehouse_receipt": ("Shipping & Documentation", "Warehouse Receipt Reference", "WR", False, False, False, None, 5, "FY"),
    "tracking_ref": ("Shipping & Documentation", "Internal Shipment Tracking Reference", "TRK", False, True, False, None, 6, "FY"),
    "invoice": ("Finance & Accounts", "Sales Invoice", "INV", True, False, True, "invoice", 5, "FY"),
    "debit_note": ("Finance & Accounts", "Debit Note", "DN", True, False, True, "debit_note", 5, "FY"),
    "credit_note": ("Finance & Accounts", "Credit Note", "CN", True, False, True, "credit_note", 5, "FY"),
    "purchase_invoice": ("Finance & Accounts", "Purchase Invoice", "PI", True, False, True, "purchase_invoice", 5, "FY"),
    "purchase_credit_note": ("Finance & Accounts", "Purchase Credit Note", "PCN", True, False, True, "purchase_credit_note", 5, "FY"),
    "receipt": ("Finance & Accounts", "Receipt Voucher / Customer Receipt", "RCP", True, False, True, "receipt", 5, "FY"),
    "payment": ("Finance & Accounts", "Payment Voucher / Vendor Payment", "PAY", True, False, True, "payment", 5, "FY"),
    "journal_voucher": ("Finance & Accounts", "Journal Voucher", "JV", True, False, False, None, 5, "FY"),
    "contra_voucher": ("Finance & Accounts", "Contra Voucher", "CV", True, False, False, None, 5, "FY"),
    "expense_voucher": ("Finance & Accounts", "Expense Voucher", "EV", True, False, False, None, 5, "FY"),
    "petty_cash_voucher": ("Finance & Accounts", "Petty Cash Voucher", "PC", True, False, False, None, 5, "FY"),
    "advance_receipt": ("Finance & Accounts", "Advance Receipt", "ADR", True, False, False, None, 5, "FY"),
    "advance_payment": ("Finance & Accounts", "Advance Payment", "ADP", True, False, False, None, 5, "FY"),
    "refund_voucher": ("Finance & Accounts", "Refund Voucher", "RF", True, False, False, None, 5, "FY"),
    "adjustment_voucher": ("Finance & Accounts", "Adjustment Voucher", "ADJ", True, False, False, None, 5, "FY"),
}
CRM_TYPES = ("lead", "enquiry", "opportunity", "followup", "crm_booking")


def info(doc_type: str) -> dict:
    if doc_type not in REGISTRY:
        raise HTTPException(422, "Unknown document type")
    module, label, code, financial, ship, integrated, legacy, width, reset = REGISTRY[doc_type]
    return {"doc_type": doc_type, "module": module, "label": label, "doc_code": code, "financial": financial, "shipment_aware": ship, "integrated": integrated, "legacy": legacy, "width": width, "reset": reset,
            "max_length": FINANCIAL_MAX if financial else DEFAULT_MAX}


def registry_list() -> list[dict]:
    return [info(k) for k in REGISTRY]


def fiscal_year_code(on: date) -> str:
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def fy_start_year(on: date) -> int:
    return on.year if on.month >= 4 else on.year - 1


def fy_text(on: date, fmt: str) -> str:
    s = fy_start_year(on)
    return {"YY-YY": f"{s % 100:02d}-{(s + 1) % 100:02d}", "YYYY-YY": f"{s}-{(s + 1) % 100:02d}", "FYYY": f"FY{s % 100:02d}", "YYYY": str(s), "YY": f"{s % 100:02d}"}.get(fmt, f"{s % 100:02d}-{(s + 1) % 100:02d}")


def today() -> date:
    return datetime.now(timezone.utc).date()


# ------------------------------------------------------------------ defaults and formats
def default_components(doc_type: str) -> list[dict]:
    i = info(doc_type)
    if doc_type == "job":
        return [{"k": "shipment"}, {"k": "sep", "v": "-"}, {"k": "cyear2"}, {"k": "sep", "v": "-"}, {"k": "number"}]
    return [{"k": "prefix", "v": i["doc_code"]}, {"k": "sep", "v": "/"}, {"k": "fy"}, {"k": "sep", "v": "/"}, {"k": "number"}]


def event(db: Session, org_id: int, config_id: int | None, action: str, user: User | None = None, *, doc_type=None, previous=None, new=None, reason=None, related_ref=None, result="SUCCESS") -> None:
    db.add(SequenceEvent(organization_id=org_id, config_id=config_id, doc_type=doc_type, action=action, previous=previous, new=new, reason=reason, user_id=user.id if user else None,
                         actor_name=(f"{user.first_name} {user.last_name}".strip() or user.username) if user else "System", related_ref=related_ref, result=result))


def log_failure(org_id: int, config_id: int | None, action: str, doc_type: str, detail: dict, user_id: int | None = None) -> None:
    """Failures are audited in their own transaction so they survive the caller's rollback."""
    s = SessionLocal()
    try:
        s.add(SequenceEvent(organization_id=org_id, config_id=config_id, doc_type=doc_type, action=action, new=detail, user_id=user_id, actor_name="System", result="FAILED"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def config_snapshot(c: SequenceConfig) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in {
        "name": c.name, "doc_type": c.doc_type, "branch_scope": c.branch_scope, "fy_scope": c.fy_scope, "shipment_scope": c.shipment_scope, "components": c.components, "number_width": c.number_width,
        "start_number": c.start_number, "step": c.step, "reset_policy": c.reset_policy, "max_length": c.max_length, "doc_code": c.doc_code, "fy_format": c.fy_format, "shipment_codes": c.shipment_codes,
        "status": c.status, "effective_from": c.effective_from, "effective_to": c.effective_to, "version": c.version}.items()}


def base_of(key: str | None) -> str:
    key = key or ""
    return "Cross-Trade" if "Cross-Trade" in key else " ".join(key.split()[:2])


def legacy_code(key: str | None) -> str:
    parts = (key or "").split()
    if len(parts) >= 2:
        return LEGACY_MODE.get(parts[0], "GEN") + LEGACY_DIR.get(parts[1], "GEN")
    return "GEN"


def shipment_code(cfg, key: str | None) -> str:
    """Code of the shipment type: the sequence's own mapping, then the standard code (system defaults keep the legacy MODE+DIRECTION code)."""
    key = key or ""
    if getattr(cfg, "is_system_default", False):
        return legacy_code(key)
    codes = {**DEFAULT_SHIPMENT_CODES, **(cfg.shipment_codes or {})}
    if key in codes:
        return codes[key]
    if base_of(key) in codes:
        return codes[base_of(key)]
    return legacy_code(key)


def render(cfg, number: int, on: date, branch_code: str | None, shipment_key: str | None) -> str:
    out = []
    for comp in cfg.components or []:
        k, v = comp.get("k"), str(comp.get("v") or "")
        if k in ("prefix", "suffix", "custom", "sep"):
            out.append(v)
        elif k == "branch":
            if not branch_code:
                raise HTTPException(409, "This sequence uses the branch code but no branch is known for this request. Set the user's branch or use a sequence without the branch component")
            out.append(branch_code.upper())
        elif k == "shipment":
            out.append(shipment_code(cfg, shipment_key))
        elif k == "doctype":
            out.append((cfg.doc_code or info(cfg.doc_type)["doc_code"]).upper())
        elif k == "fy":
            out.append(fy_text(on, cfg.fy_format))
        elif k == "cyear":
            out.append(str(on.year))
        elif k == "cyear2":
            out.append(f"{on.year % 100:02d}")
        elif k == "number":
            out.append(str(number).zfill(cfg.number_width))
    return "".join(out)


def branch_codes(db: Session, org_id: int) -> list[str]:
    return [r.code.upper() for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "branch", MasterRecord.status == "ACTIVE"))]


def validate(db: Session, org_id: int, cfg, for_activation: bool = False) -> dict:
    """Validate a config (an object with the SequenceConfig attributes). Returns errors, warnings and the worst-case identifier length."""
    errors: list[str] = []
    warnings: list[str] = []
    i = info(cfg.doc_type)
    comps = cfg.components or []
    if not comps or len(comps) > 14:
        errors.append("The format needs between 1 and 14 components")
    kinds = [c.get("k") for c in comps]
    if kinds.count("number") != 1:
        errors.append("The format must contain exactly one Sequential Number component")
    for c in comps:
        k, v = c.get("k"), str(c.get("v") or "")
        if k not in COMPONENT_TYPES:
            errors.append(f"Unknown component '{k}'")
        elif k == "sep" and (len(v) != 1 or v not in ALLOWED_SEPARATORS):
            errors.append(f"Separator must be one of {' '.join(ALLOWED_SEPARATORS)}")
        elif k in ("prefix", "suffix", "custom") and not re.fullmatch(r"[A-Za-z0-9]{1,12}", v):
            errors.append(f"The {k} must be 1 to 12 letters or digits")
    if cfg.reset_policy not in RESET_POLICIES:
        errors.append("Reset policy must be NEVER, FY or CALENDAR")
    if cfg.fy_format not in FY_FORMATS:
        errors.append("Unknown financial year format")
    if not (1 <= cfg.number_width <= 12):
        errors.append("Number width must be between 1 and 12 digits")
    if cfg.step < 1 or cfg.step > 1000:
        errors.append("Increment step must be between 1 and 1000")
    if cfg.start_number < 0:
        errors.append("Starting number cannot be negative")
    if cfg.start_number > 10 ** cfg.number_width - 1:
        errors.append("Starting number does not fit in the number width")
    cap = FINANCIAL_MAX if i["financial"] else HARD_MAX
    if cfg.max_length > cap:
        errors.append(f"The maximum identifier length for {i['label']} is {cap} characters" + (" (invoices and vouchers)" if i["financial"] else " (database limit)"))
    if cfg.max_length < 4:
        errors.append("Maximum length is too small")
    if cfg.effective_from and cfg.effective_to and cfg.effective_to < cfg.effective_from:
        errors.append("Effective end date cannot be before the start date")
    codes = branch_codes(db, org_id)
    if cfg.branch_scope != "ALL" and cfg.branch_scope not in codes:
        errors.append(f"Branch '{cfg.branch_scope}' does not exist in the Branch master")
    if cfg.fy_scope != "ALL" and not re.fullmatch(r"\d{2}-\d{2}", cfg.fy_scope):
        errors.append("Financial year scope must look like 26-27")
    if cfg.shipment_scope != "ALL" and cfg.shipment_scope not in SHIPMENT_KEYS:
        errors.append("Unknown shipment type scope")
    if cfg.shipment_scope != "ALL" and not i["shipment_aware"]:
        errors.append(f"{i['label']} does not depend on the shipment type")
    if "shipment" in kinds and not i["shipment_aware"]:
        errors.append(f"The shipment type component cannot be used for {i['label']}")
    for code in (cfg.shipment_codes or {}).values():
        if not CODE_RE.fullmatch(str(code)):
            errors.append(f"Shipment code '{code}' must be 1 to 12 capital letters or digits")
    if cfg.doc_code and not CODE_RE.fullmatch(cfg.doc_code):
        errors.append("Document type code must be 1 to 12 capital letters or digits")
    # worst case: widest number, longest branch / shipment / year text that can occur in this scope
    worst = 0
    if not errors:
        branches = [cfg.branch_scope] if cfg.branch_scope != "ALL" else (codes or ["BRANCH"])
        ships = [cfg.shipment_scope] if cfg.shipment_scope != "ALL" else (SHIPMENT_KEYS if "shipment" in kinds else [None])
        for b, sh in product(branches, ships):
            n = 10 ** cfg.number_width - 1
            try:
                s = render(cfg, n, date(2026, 12, 31), b, sh)
            except HTTPException:
                continue
            worst = max(worst, len(s))
            if not FINAL_RE.fullmatch(s):
                errors.append("The generated identifier would contain unsupported characters")
                break
        if worst > cfg.max_length:
            errors.append(f"{i['label']} sequence exceeds the maximum permitted length of {cfg.max_length} characters. Current length: {worst}. Please shorten the prefix, branch code, year code, separators, suffix or numeric sequence width.")
        elif worst > cfg.max_length - 2 and cfg.max_length == FINANCIAL_MAX:
            warnings.append(f"Worst-case length is {worst} of {cfg.max_length} characters: there is no room left for a longer branch code")
    if "branch" in kinds and not codes and cfg.branch_scope == "ALL":
        warnings.append("No branches exist in the Branch master yet, so the branch component cannot be filled. Add branches before activating")
    return {"errors": errors, "warnings": warnings, "worst_length": worst, "max_length": cfg.max_length, "ok": not errors}


class _Draft:
    """A throw-away config built from request data, used for preview/validation without saving."""
    def __init__(self, data: dict):
        d = {"name": "", "branch_scope": "ALL", "fy_scope": "ALL", "shipment_scope": "ALL", "components": [], "number_width": 5, "start_number": 1, "step": 1, "reset_policy": "FY", "max_length": DEFAULT_MAX, "doc_code": None,
             "fy_format": "YY-YY", "shipment_codes": {}, "effective_from": None, "effective_to": None, "version": 1, "status": "DRAFT", **data}
        for k, v in d.items():
            setattr(self, k, v)
        self.id = None


def sample_numbers(db: Session, org_id: int, cfg, count: int = 4, on: date | None = None, first: int | None = None) -> list[dict]:
    """Simulated numbers: nothing is consumed or reserved."""
    on = on or today()
    codes = branch_codes(db, org_id)
    branch = cfg.branch_scope if cfg.branch_scope != "ALL" else (codes[0] if codes else None)
    ship = cfg.shipment_scope if cfg.shipment_scope != "ALL" else ("Sea Export" if "shipment" in [c.get("k") for c in cfg.components or []] else None)
    n = first if first is not None else cfg.start_number
    out = []
    for _ in range(count):
        try:
            s = render(cfg, n, on, branch, ship)
            out.append({"number": s, "length": len(s), "ok": len(s) <= cfg.max_length})
        except HTTPException as exc:
            out.append({"number": None, "length": 0, "ok": False, "error": exc.detail})
        n += cfg.step
    return out


# ------------------------------------------------------------------ resolution and issuing
def user_branch(db: Session, user: User | None) -> str | None:
    """The branch code of the signed-in user (profile 'branch' matched against the Branch master by code or name)."""
    if user is None or user.organization_id is None:
        return None
    raw = str((user.profile or {}).get("branch") or "").strip().lower()
    if not raw:
        return None
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == user.organization_id, MasterRecord.master_type == "branch", MasterRecord.status == "ACTIVE")):
        if r.code.lower() == raw or r.name.lower() == raw:
            return r.code.upper()
    return None


def ensure_default(db: Session, org_id: int, doc_type: str) -> SequenceConfig:
    i = info(doc_type)
    cfg = SequenceConfig(organization_id=org_id, name=f"{i['label']} (default)", module=i["module"], doc_type=doc_type, components=default_components(doc_type), number_width=i["width"], start_number=1, step=1,
                         reset_policy=i["reset"], max_length=i["max_length"], doc_code=i["doc_code"], fy_format="YY-YY", shipment_codes={}, status="ACTIVE", is_system_default=True, version=1,
                         remarks="Created automatically; reproduces the numbering used before Sequence Setting existed.")
    db.add(cfg)
    db.flush()
    event(db, org_id, cfg.id, "DEFAULT_CREATED", None, doc_type=doc_type, new=config_snapshot(cfg))
    return cfg


def period_key(cfg, on: date) -> str:
    return "ALL" if cfg.reset_policy == "NEVER" else (fiscal_year_code(on) if cfg.reset_policy == "FY" else str(on.year))


def _matches(cfg: SequenceConfig, on: date, branch: str | None, ship: str | None) -> int | None:
    if cfg.effective_from and on < cfg.effective_from or cfg.effective_to and on > cfg.effective_to:
        return None
    score = 0
    if cfg.branch_scope != "ALL":
        if cfg.branch_scope != (branch or ""):
            return None
        score += 8
    if cfg.fy_scope != "ALL":
        if cfg.fy_scope != fiscal_year_code(on):
            return None
        score += 1
    if cfg.shipment_scope != "ALL":
        key = ship or ""
        if cfg.shipment_scope == key:
            score += 4
        elif cfg.shipment_scope == base_of(key):
            score += 2
        else:
            return None
    return score


def resolve(db: Session, org_id: int, doc_type: str, on: date, branch: str | None, ship: str | None, user_id: int | None = None) -> SequenceConfig:
    info(doc_type)
    configs = list(db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == org_id, SequenceConfig.doc_type == doc_type)))
    if not any(c.status != "DRAFT" for c in configs):  # nothing has ever been active: start with the legacy-compatible default
        # serialise the creation so simultaneous first requests cannot create two defaults
        db.execute(text("SELECT pg_advisory_xact_lock(CAST(:k AS bigint))"), {"k": (org_id << 32) | zlib.crc32(doc_type.encode())})
        configs = list(db.scalars(select(SequenceConfig).where(SequenceConfig.organization_id == org_id, SequenceConfig.doc_type == doc_type).execution_options(populate_existing=True)))
        if not any(c.status != "DRAFT" for c in configs):
            return ensure_default(db, org_id, doc_type)
    hits = [(s, c) for c in configs if c.status == "ACTIVE" and (s := _matches(c, on, branch, ship)) is not None]
    if not hits:
        log_failure(org_id, None, "SEQUENCE_MISSING", doc_type, {"branch": branch, "shipment": ship, "date": on.isoformat()}, user_id)
        raise HTTPException(409, f"No active {info(doc_type)['label']} sequence applies (branch {branch or 'not set'}, shipment {ship or 'any'}, {fiscal_year_code(on)}). Configure one in Settings → Sequence Setting; no number was issued.")
    best = max(s for s, _ in hits)
    top = [c for s, c in hits if s == best]
    if len(top) > 1:
        log_failure(org_id, None, "SEQUENCE_CONFLICT", doc_type, {"configs": [c.id for c in top]}, user_id)
        raise HTTPException(409, f"Sequence configuration conflict for {info(doc_type)['label']}: {len(top)} active sequences match equally ({', '.join(c.name for c in top)}). Deactivate one of them; no number was issued.")
    return top[0]


def exists_in_business(db: Session, org_id: int, doc_type: str, number: str) -> bool:
    q = None
    if doc_type == "job":
        q = select(Shipment.id).where(Shipment.organization_id == org_id, Shipment.job_number == number)
    elif doc_type == "quotation":
        q = select(Quotation.id).where(Quotation.organization_id == org_id, Quotation.number == number)
    elif doc_type == "booking":
        q = select(Booking.id).where(Booking.organization_id == org_id, Booking.number == number)
    elif doc_type in ("invoice", "debit_note", "credit_note", "purchase_invoice", "purchase_credit_note"):
        q = select(Document.id).where(Document.organization_id == org_id, Document.doc_type == doc_type, Document.doc_number == number)
    elif doc_type in ("receipt", "payment"):
        q = select(Payment.id).where(Payment.organization_id == org_id, Payment.pay_number == number)
    return bool(q is not None and db.scalar(q.limit(1)))


def _counter(db: Session, cfg: SequenceConfig, period: str) -> SequenceCounter:
    db.execute(pg_insert(SequenceCounter).values(config_id=cfg.id, period_key=period, last_number=-1).on_conflict_do_nothing(constraint="uq_sequence_counter"))
    c = db.scalar(select(SequenceCounter).where(SequenceCounter.config_id == cfg.id, SequenceCounter.period_key == period).with_for_update())
    if c.last_number == -1:  # first use of this counter
        start = cfg.start_number - cfg.step
        legacy = info(cfg.doc_type)["legacy"]
        if cfg.is_system_default and legacy:  # continue the legacy counter: never repeat an already issued number
            seq = db.scalar(select(DocSequence.last_number).where(DocSequence.organization_id == cfg.organization_id, DocSequence.seq_type == legacy, DocSequence.fiscal_year == period))
            if seq is not None:
                start = max(start, seq)
        c.last_number = start
    return c


def issue(db: Session, org_id: int, doc_type: str, on: date | None = None, user: User | None = None, branch_code: str | None = None, shipment_key: str | None = None, related_ref: str | None = None) -> str:
    on = on or today()
    branch = (branch_code or user_branch(db, user) or None)
    branch = branch.upper() if branch else None
    cfg = resolve(db, org_id, doc_type, on, branch, shipment_key, user.id if user else None)
    period = period_key(cfg, on)
    counter = _counter(db, cfg, period)
    limit = 10 ** cfg.number_width - 1
    for _ in range(60):
        nxt = counter.last_number + cfg.step
        if nxt > limit:
            log_failure(org_id, cfg.id, "COUNTER_EXHAUSTED", doc_type, {"period": period, "width": cfg.number_width}, user.id if user else None)
            raise HTTPException(409, f"The {info(doc_type)['label']} sequence '{cfg.name}' is exhausted for {period}: the number no longer fits in {cfg.number_width} digits. Configure a wider sequence; no number was issued.")
        number = render(cfg, nxt, on, branch, shipment_key)
        if len(number) > cfg.max_length or not FINAL_RE.fullmatch(number):
            log_failure(org_id, cfg.id, "LENGTH_EXCEEDED", doc_type, {"number": number, "length": len(number), "max": cfg.max_length}, user.id if user else None)
            raise HTTPException(409, f"The generated {info(doc_type)['label']} '{number}' would be {len(number)} characters (maximum {cfg.max_length}). Shorten the sequence format; no number was issued.")
        counter.last_number = nxt
        taken = db.scalar(select(IssuedNumber.id).where(IssuedNumber.organization_id == org_id, IssuedNumber.doc_type == doc_type, IssuedNumber.number == number)) or exists_in_business(db, org_id, doc_type, number)
        if taken:
            log_failure(org_id, cfg.id, "DUPLICATE_REJECTED", doc_type, {"number": number}, user.id if user else None)
            continue  # an identifier that already exists is never reused: skip it
        db.add(IssuedNumber(organization_id=org_id, doc_type=doc_type, number=number, config_id=cfg.id, config_version=cfg.version, period_key=period, branch_code=branch, shipment_key=shipment_key, issued_by=user.id if user else None, related_type=related_ref))
        db.flush()
        return number
    raise HTTPException(409, f"Could not find a free {info(doc_type)['label']} number after 60 attempts; check the sequence start number against existing records")


def bind(db: Session, org_id: int, doc_type: str, number: str, related_type: str, related_id) -> None:
    row = db.scalar(select(IssuedNumber).where(IssuedNumber.organization_id == org_id, IssuedNumber.doc_type == doc_type, IssuedNumber.number == number))
    if row is not None:
        row.related_type, row.related_id = related_type, str(related_id)


def next_preview(db: Session, cfg: SequenceConfig, on: date | None = None) -> str | None:
    """The number the next real issue would produce (read-only; consumes nothing)."""
    on = on or today()
    period = period_key(cfg, on)
    c = db.scalar(select(SequenceCounter).where(SequenceCounter.config_id == cfg.id, SequenceCounter.period_key == period))
    last = c.last_number if c and c.last_number != -1 else None
    if last is None:
        last = cfg.start_number - cfg.step
        legacy = info(cfg.doc_type)["legacy"]
        if cfg.is_system_default and legacy:
            seq = db.scalar(select(DocSequence.last_number).where(DocSequence.organization_id == cfg.organization_id, DocSequence.seq_type == legacy, DocSequence.fiscal_year == period))
            if seq is not None:
                last = max(last, seq)
    codes = branch_codes(db, cfg.organization_id)
    branch = cfg.branch_scope if cfg.branch_scope != "ALL" else (codes[0] if codes else None)
    ship = cfg.shipment_scope if cfg.shipment_scope != "ALL" else ("Sea Export" if any(x.get("k") == "shipment" for x in cfg.components or []) else None)
    try:
        return render(cfg, last + cfg.step, on, branch, ship)
    except HTTPException:
        return None


def suggestions(db: Session, org_id: int, doc_type: str) -> list[dict]:
    """Suggested formats with the reasoning, previewed with the company's real branch codes and the current financial year."""
    i = info(doc_type)
    codes = branch_codes(db, org_id)
    longest = max((len(c) for c in codes), default=3)
    out = []
    code = i["doc_code"]
    fy = "YY-YY"
    cap = i["max_length"]

    def mk(name, comps, width, reset, why, fy_fmt="YY-YY"):
        cfg = _Draft({"doc_type": doc_type, "components": comps, "number_width": width, "reset_policy": reset, "max_length": cap, "doc_code": code, "fy_format": fy_fmt, "name": name})
        v = validate(db, org_id, cfg)
        out.append({"name": name, "components": comps, "number_width": width, "reset_policy": reset, "fy_format": fy_fmt, "max_length": cap, "doc_code": code, "reason": why, "valid": v["ok"],
                    "worst_length": v["worst_length"], "errors": v["errors"], "samples": sample_numbers(db, org_id, cfg, 3)})

    sep = lambda c="/": {"k": "sep", "v": c}
    if i["financial"]:
        mk("Document code / year / number", [{"k": "doctype"}, sep(), {"k": "fy"}, sep(), {"k": "number"}], 5, "FY", f"Short and legally clean: the whole identifier stays within the {cap}-character limit and restarts every financial year.")
        if codes:
            mk("Branch / document code / number", [{"k": "branch"}, sep("-"), {"k": "doctype"}, sep("-"), {"k": "number"}], 5, "FY", f"Branch-wise numbering using your branch codes (longest is {longest} characters), without a year so there is room for a wider number.")
        mk("Document code / short year / number", [{"k": "doctype"}, sep("/"), {"k": "fy", "v": ""}, sep("/"), {"k": "number"}], 4, "FY", "Shortest option: four-digit counter, suited to low volumes.", fy_fmt="YY")
    elif i["shipment_aware"]:
        mk("Shipment type / year / number", [{"k": "shipment"}, sep("-"), {"k": "fy"}, sep("-"), {"k": "number"}], 5, "FY", "Each shipment type is recognisable at a glance and numbering restarts every financial year.")
        if codes:
            mk("Branch / shipment type / year / number", [{"k": "branch"}, sep("/"), {"k": "shipment"}, sep("/"), {"k": "fy"}, sep("/"), {"k": "number"}], 5, "FY", "Branch-wise job numbers such as the branch code, shipment code, year and a running number.")
        mk("Prefix / shipment type / number (never resets)", [{"k": "prefix", "v": "JB"}, sep("/"), {"k": "shipment"}, sep("/"), {"k": "number"}], 6, "NEVER", "A single running series per shipment type that never restarts.")
    else:
        mk("Document code / year / number", [{"k": "doctype"}, sep("/"), {"k": "fy"}, sep("/"), {"k": "number"}], 5, "FY", "Matches the numbering style already used for quotations and bookings.")
        if codes:
            mk("Branch / document code / year / number", [{"k": "branch"}, sep("/"), {"k": "doctype"}, sep("/"), {"k": "fy"}, sep("/"), {"k": "number"}], 5, "FY", "Branch-wise numbering so each branch has its own recognisable series.")
        mk("Document code / calendar year / number", [{"k": "doctype"}, sep("-"), {"k": "cyear"}, sep("-"), {"k": "number"}], 5, "CALENDAR", "Uses the calendar year and restarts every January.")
    return out
