"""Chart of Accounts: the group tree, the ledgers and the rules that create ledgers from the masters.

Structure (Level 1 main group -> Level 2 sub group -> nested sub groups -> Level 4 ledger):
    ASSETS / LIABILITIES / INCOME / EXPENSES, each with default sub groups; a company can add sub groups under any group.
A ledger always sits under a group and gets a system generated code made of the group prefix and a running number (ACSC0001, LCSC0002, IC0001 ...).
Masters create their ledgers: customers under Sundry Debtors, vendors under Sundry Creditors, charges under Direct Incomes / Direct Expenses,
employees under Staff Salary, banks under Bank Accounts.
"""
from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AccGroup, AccLedger, Customer, MasterRecord

# (name, prefix, children)  - a child without its own prefix uses its parent's
DEFAULT_TREE = {
    "ASSETS": ("A", "ASSETS", [
        ("CURRENT ASSETS", "AC", [("BANK ACCOUNTS", "ACBC", []), ("CASH-IN-HAND", "ACCH", []), ("DEPOSIT(Asset)", "ACDP", []), ("DUTIES & TAXES", "ACDT", []), ("LOANS & ADVANCES", "ACLA", []),
                                  ("PREPAID EXPENSES", "ACPE", []), ("SUNDRY DEBTORS", "ACSC", []), ("TDS RECEIVABLES", "ACTR", [])]),
        ("FIXED ASSETS", "AF", []), ("INVESTMENTS", "AI", [])]),
    "LIABILITIES": ("L", "LIABILITIES", [
        ("BRANCH / DIVISIONS", "LB", []), ("CAPITAL ACCOUNT", "CAP", []),
        ("CURRENT LIABILITIES", "LC", [("SUNDRY CREDITORS", "LCSC", []), ("DUTIES & TAXES", "DT", []), ("TDS PAYABLE", "LCTP", [])]),
        ("LOAN (LIABILITY)", "LL", [])]),
    "INCOME": ("I", "INCOME", [
        ("DIRECT INCOMES", "IC", [("CHA", "IC", []), ("TRANSPORT", "IC", []), ("FREIGHT FORWARDING", "IC", [])]),
        ("INDIRECT INCOMES", "II", []), ("SALES ACCOUNTS", "IS", []), ("OTHER INCOME", "IO", [])]),
    "EXPENSES": ("E", "EXPENSES", [
        ("DIRECT EXPENSES", "ED", [("CHA", "ED", []), ("TRANSPORT", "ED", []), ("FREIGHT FORWARDING", "ED", [])]),
        ("INDIRECT EXPENSES", "EI", [("STAFF SALARY", "EXP", [])]), ("PURCHASE ACCOUNTS", "EP", []), ("OTHER EXPENSES", "EO", [])]),
}

# system ledgers every company needs for posting: key -> (name, path of groups)
SYSTEM_LEDGERS = {
    "cash": ("Cash in Hand", ("ASSETS", "CURRENT ASSETS", "CASH-IN-HAND")),
    "out_cgst": ("Output CGST", ("LIABILITIES", "CURRENT LIABILITIES", "DUTIES & TAXES")),
    "out_sgst": ("Output SGST", ("LIABILITIES", "CURRENT LIABILITIES", "DUTIES & TAXES")),
    "out_igst": ("Output IGST", ("LIABILITIES", "CURRENT LIABILITIES", "DUTIES & TAXES")),
    "in_cgst": ("Input CGST", ("ASSETS", "CURRENT ASSETS", "DUTIES & TAXES")),
    "in_sgst": ("Input SGST", ("ASSETS", "CURRENT ASSETS", "DUTIES & TAXES")),
    "in_igst": ("Input IGST", ("ASSETS", "CURRENT ASSETS", "DUTIES & TAXES")),
    "tds_recv": ("TDS Receivable", ("ASSETS", "CURRENT ASSETS", "TDS RECEIVABLES")),
    "tds_pay": ("TDS Payable", ("LIABILITIES", "CURRENT LIABILITIES", "TDS PAYABLE")),
    "tcs_recv": ("TCS Receivable", ("ASSETS", "CURRENT ASSETS", "TDS RECEIVABLES")),
    "tcs_pay": ("TCS Payable", ("LIABILITIES", "CURRENT LIABILITIES", "TDS PAYABLE")),
    "round_off": ("Round Off", ("EXPENSES", "INDIRECT EXPENSES")),
    "pure_agent": ("Pure Agent Reimbursement", ("LIABILITIES", "CURRENT LIABILITIES")),
    "capital": ("Capital Account", ("LIABILITIES", "CAPITAL ACCOUNT")),
    "reserve": ("Profit & Loss Account (Reserves)", ("LIABILITIES", "CAPITAL ACCOUNT")),
    "inc_ff": ("Freight Forwarding Income", ("INCOME", "DIRECT INCOMES", "FREIGHT FORWARDING")),
    "inc_cha": ("CHA Income", ("INCOME", "DIRECT INCOMES", "CHA")),
    "inc_trn": ("Transport Income", ("INCOME", "DIRECT INCOMES", "TRANSPORT")),
    "inc_oth": ("Other Operating Income", ("INCOME", "INDIRECT INCOMES")),
    "exp_ff": ("Freight Forwarding Expenses", ("EXPENSES", "DIRECT EXPENSES", "FREIGHT FORWARDING")),
    "exp_cha": ("CHA Expenses", ("EXPENSES", "DIRECT EXPENSES", "CHA")),
    "exp_trn": ("Transport Expenses", ("EXPENSES", "DIRECT EXPENSES", "TRANSPORT")),
    "exp_oth": ("Other Direct Expenses", ("EXPENSES", "DIRECT EXPENSES")),
}
NATURE_DR = ("ASSETS", "EXPENSES")   # normal debit balance


def _find_group(db: Session, org_id: int, path: tuple[str, ...]) -> AccGroup | None:
    parent = None
    for name in path:
        pid = AccGroup.parent_id.is_(None) if parent is None else AccGroup.parent_id == parent.id
        g = db.scalar(select(AccGroup).where(AccGroup.organization_id == org_id, AccGroup.name == name, pid))
        if g is None:
            return None
        parent = g
    return parent


def ensure_tree(db: Session, org_id: int) -> None:
    """Load the default chart of accounts for the company (idempotent: existing groups and ledgers are never changed)."""
    if db.scalar(select(func.count()).select_from(AccGroup).where(AccGroup.organization_id == org_id)) == 0:
        def add(name, prefix, kids, parent, nature, order):
            g = AccGroup(organization_id=org_id, parent_id=parent.id if parent else None, name=name, code=prefix, nature=nature, is_system=True, sort_order=order)
            db.add(g)
            db.flush()
            for i, (n, p, k) in enumerate(kids):
                add(n, p, k, g, nature, i)
            return g

        for i, (main, (prefix, _n, kids)) in enumerate(DEFAULT_TREE.items()):
            add(main, prefix, kids, None, main, i)
    for key, (name, path) in SYSTEM_LEDGERS.items():
        if db.scalar(select(AccLedger.id).where(AccLedger.organization_id == org_id, AccLedger.system_key == key)):
            continue
        g = _find_group(db, org_id, path)
        if g is not None:
            _make_ledger(db, org_id, g, name, system_key=key, control=key in ("cash", "tds_recv", "tds_pay"))
    _control_accounts(db, org_id)
    db.flush()


CONTROL_ACCOUNTS = (("ASSETS", "CURRENT ASSETS", "SUNDRY DEBTORS", "Sundry Debtors (AR control)"), ("LIABILITIES", "CURRENT LIABILITIES", "SUNDRY CREDITORS", "Sundry Creditors (AP control)"))


def _control_accounts(db: Session, org_id: int) -> None:
    """The Organization Master maps a customer / vendor to an AR / AP control account. Those are the Sundry Debtors and Sundry Creditors groups of the chart."""
    for main, sub, name, label in CONTROL_ACCOUNTS:
        g = _find_group(db, org_id, (main, sub, name))
        if g is None or db.scalar(select(MasterRecord.id).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "account", MasterRecord.code == g.code)):
            continue
        db.add(MasterRecord(organization_id=org_id, master_type="account", code=g.code, name=label, status="ACTIVE", data={"group": name, "account_type": "control", "source": "chart_of_accounts"}))


def group_path(db: Session, g: AccGroup) -> list[AccGroup]:
    out = [g]
    while out[0].parent_id:
        out.insert(0, db.get(AccGroup, out[0].parent_id))
    return out


def next_code(db: Session, org_id: int, prefix: str) -> str:
    best = 0
    for (code,) in db.execute(select(AccLedger.code).where(AccLedger.organization_id == org_id, AccLedger.code.like(prefix + "%"))):
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", code or "")
        if m:
            best = max(best, int(m.group(1)))
    return f"{prefix}{best + 1:04d}"


def _make_ledger(db: Session, org_id: int, group: AccGroup, name: str, *, system_key=None, source_type=None, source_id=None, source_role=None, control=False, user_id=None, alias=None, currency="INR") -> AccLedger:
    led = AccLedger(organization_id=org_id, group_id=group.id, code=next_code(db, org_id, group.code), name=name.strip()[:200], alias=alias, control=control, system_key=system_key, source_type=source_type, source_id=source_id,
                    source_role=source_role, currency=currency, created_by=user_id)
    db.add(led)
    db.flush()
    return led


def sys_ledger(db: Session, org_id: int, key: str) -> AccLedger:
    ensure_tree(db, org_id)
    led = db.scalar(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.system_key == key))
    if led is None:
        raise ValueError(f"System ledger {key} is missing from the chart of accounts")
    return led


def party_ledger(db: Session, org_id: int, name: str, role: str, customer: Customer | None = None, user_id=None) -> AccLedger:
    """The debtor (role 'debtor') or creditor (role 'creditor') ledger of a party; created under Sundry Debtors / Sundry Creditors when it does not exist yet."""
    ensure_tree(db, org_id)
    if customer is None:
        customer = db.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == name))
    q = select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.source_role == role)
    led = db.scalar(q.where(AccLedger.source_type == "customer", AccLedger.source_id == customer.id)) if customer else db.scalar(q.where(AccLedger.name == name))
    if led is None and customer is None:
        led = db.scalar(q.where(AccLedger.name == name))
    if led is not None:
        return led
    path = ("ASSETS", "CURRENT ASSETS", "SUNDRY DEBTORS") if role == "debtor" else ("LIABILITIES", "CURRENT LIABILITIES", "SUNDRY CREDITORS")
    g = _find_group(db, org_id, path)
    return _make_ledger(db, org_id, g, name, source_type="customer" if customer else "party", source_id=customer.id if customer else None, source_role=role, user_id=user_id)


CLIENT_ROLES = {"shipper", "consignee", "ar", "client"}
VENDOR_ROLES = {"carrier", "agent", "cha", "transporter", "ap", "vendor"}


def sync_customer(db: Session, customer: Customer, user_id=None) -> list[AccLedger]:
    """Customer / vendor master -> ledger(s): a client gets a debtor ledger, a vendor a creditor ledger, a party that is both gets both."""
    roles = {str(r).lower() for r in (customer.roles or [])}
    made = []
    if roles & CLIENT_ROLES or not roles & VENDOR_ROLES:
        made.append(party_ledger(db, customer.organization_id, customer.name, "debtor", customer, user_id))
    if roles & VENDOR_ROLES:
        made.append(party_ledger(db, customer.organization_id, customer.name, "creditor", customer, user_id))
    for led in made:   # a renamed party renames its ledgers
        if led.name != customer.name[:200]:
            led.name = customer.name[:200]
        led.status = "ACTIVE" if customer.status == "ACTIVE" else "INACTIVE"
    return made


LOB_OF_MODULE = {"FOR": "FREIGHT FORWARDING", "FF": "FREIGHT FORWARDING", "CUS": "CHA", "CHA": "CHA", "CCL": "CHA", "TRN": "TRANSPORT", "TRANSPORT": "TRANSPORT"}


def _lob(rec: MasterRecord) -> str:
    d = rec.data or {}
    mod = str(d.get("module") or "").upper()
    cat = str(d.get("category") or "").lower()
    if mod in LOB_OF_MODULE:
        return LOB_OF_MODULE[mod]
    if "custom" in cat:
        return "CHA"
    if "transport" in cat or "trucking" in cat:
        return "TRANSPORT"
    return "FREIGHT FORWARDING"


def sync_charge(db: Session, rec: MasterRecord, user_id=None) -> dict:
    """Charge master -> its income ledger (Direct Incomes > line of business) and its expense ledger (Direct Expenses > line of business)."""
    ensure_tree(db, rec.organization_id)
    lob = _lob(rec)
    out = {}
    for side, main, sub in (("income", "INCOME", "DIRECT INCOMES"), ("expense", "EXPENSES", "DIRECT EXPENSES")):
        led = db.scalar(select(AccLedger).where(AccLedger.organization_id == rec.organization_id, AccLedger.source_type == "charge", AccLedger.source_id == rec.id, AccLedger.source_role == side))
        if led is None:
            g = _find_group(db, rec.organization_id, (main, sub, lob))
            led = _make_ledger(db, rec.organization_id, g, rec.name, source_type="charge", source_id=rec.id, source_role=side, user_id=user_id)
        elif led.name != rec.name[:200]:
            led.name = rec.name[:200]
        out[side] = led
    data = dict(rec.data or {})
    want = {"coa_income": f"{out['income'].code} - {out['income'].name}", "coa_expense": f"{out['expense'].code} - {out['expense'].name}"}
    if any(data.get(k) != v for k, v in want.items()):   # the Charge Master shows the ledgers the charge posts to
        rec.data = {**data, **want}
    return out


def charge_ledger(db: Session, org_id: int, charge_name: str, side: str, fallback_lob: str = "FREIGHT FORWARDING") -> AccLedger:
    """The ledger a charge line posts to: its own charge ledger, else the line-of-business ledger."""
    rec = db.scalar(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "charge", MasterRecord.name == charge_name))
    if rec is not None:
        return sync_charge(db, rec)[side]
    key = {"FREIGHT FORWARDING": "ff", "CHA": "cha", "TRANSPORT": "trn"}.get(fallback_lob, "ff")
    return sys_ledger(db, org_id, ("inc_" if side == "income" else "exp_") + key)


def sync_employee(db: Session, rec: MasterRecord, user_id=None) -> AccLedger:
    ensure_tree(db, rec.organization_id)
    led = db.scalar(select(AccLedger).where(AccLedger.organization_id == rec.organization_id, AccLedger.source_type == "employee", AccLedger.source_id == rec.id, AccLedger.source_role == "salary"))
    if led is None:
        g = _find_group(db, rec.organization_id, ("EXPENSES", "INDIRECT EXPENSES", "STAFF SALARY"))
        led = _make_ledger(db, rec.organization_id, g, "Salary - " + rec.name, source_type="employee", source_id=rec.id, source_role="salary", user_id=user_id)
    return led


def staff_advance_ledger(db: Session, rec: MasterRecord, user_id=None) -> AccLedger:
    """The employee's advance account (an asset: money the staff member owes the company), under Loans & Advances."""
    ensure_tree(db, rec.organization_id)
    led = db.scalar(select(AccLedger).where(AccLedger.organization_id == rec.organization_id, AccLedger.source_type == "employee", AccLedger.source_id == rec.id, AccLedger.source_role == "advance"))
    if led is None:
        g = _find_group(db, rec.organization_id, ("ASSETS", "CURRENT ASSETS", "LOANS & ADVANCES"))
        led = _make_ledger(db, rec.organization_id, g, "Staff Advance - " + rec.name, source_type="employee", source_id=rec.id, source_role="advance", user_id=user_id)
    return led


def sync_bank(db: Session, rec: MasterRecord, user_id=None) -> AccLedger:
    ensure_tree(db, rec.organization_id)
    led = db.scalar(select(AccLedger).where(AccLedger.organization_id == rec.organization_id, AccLedger.source_type == "bank", AccLedger.source_id == rec.id))
    label = rec.name + ((" - " + str((rec.data or {}).get("account_number"))) if (rec.data or {}).get("account_number") else "")
    if led is None:
        g = _find_group(db, rec.organization_id, ("ASSETS", "CURRENT ASSETS", "BANK ACCOUNTS"))
        led = _make_ledger(db, rec.organization_id, g, label, source_type="bank", source_id=rec.id, source_role="bank", user_id=user_id, control=True)
        ob = Decimal(str((rec.data or {}).get("opening_balance") or 0) or 0)
        if ob:
            led.opening_balance, led.opening_side = abs(ob), "Dr" if ob > 0 else "Cr"
    else:
        led.name = label[:200]
    led.status = "ACTIVE" if rec.status == "ACTIVE" else "INACTIVE"
    return led


def bank_ledger(db: Session, org_id: int, method: str, account: str | None) -> AccLedger:
    """The ledger a receipt / payment goes through: Cash in Hand for cash, otherwise the bank ledger named in the transaction, else the default bank, else a general bank ledger."""
    ensure_tree(db, org_id)
    if method == "Cash":
        return sys_ledger(db, org_id, "cash")
    banks = list(db.scalars(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.source_type == "bank", AccLedger.status == "ACTIVE").order_by(AccLedger.id)))
    if account:
        for b in banks:
            if account.strip().lower() in b.name.lower() or b.name.lower() in account.strip().lower():
                return b
    recs = {r.id: r for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "bank"))}
    for b in banks:
        if str((recs.get(b.source_id).data or {}).get("default_bank", "")).lower() in ("yes", "true", "1") if recs.get(b.source_id) else False:
            return b
    if banks:
        return banks[0]
    g = _find_group(db, org_id, ("ASSETS", "CURRENT ASSETS", "BANK ACCOUNTS"))
    led = db.scalar(select(AccLedger).where(AccLedger.organization_id == org_id, AccLedger.group_id == g.id, AccLedger.name == "Bank Account"))
    return led or _make_ledger(db, org_id, g, "Bank Account", control=True)


def sync_all(db: Session, org_id: int, user_id=None) -> dict:
    """Create the ledgers of every existing master record (used once for companies that had masters before the chart of accounts existed)."""
    ensure_tree(db, org_id)
    n = {"parties": 0, "charges": 0, "employees": 0, "banks": 0}
    for c in db.scalars(select(Customer).where(Customer.organization_id == org_id)):
        n["parties"] += len(sync_customer(db, c, user_id))
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "charge")):
        sync_charge(db, r, user_id)
        n["charges"] += 1
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "employee")):
        sync_employee(db, r, user_id)
        n["employees"] += 1
    for r in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "bank")):
        sync_bank(db, r, user_id)
        n["banks"] += 1
    db.flush()
    return n
