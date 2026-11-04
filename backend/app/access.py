"""Who may see whose records. One function, scope_for(), answers it for every module.

A user with no placement row (or an executive / platform user) is unrestricted: the ERP behaves exactly as before for them.
Placed users are narrowed by category: salesperson = own records, coordinator / sales manager = own team (the reporting line below them),
branch manager / operations / accounts = their branches, national head = the branches and regions they were given. Deny by default when a placed user has no branch.
"""
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import AccVoucher, Booking, CrmRecord, Document, Payment, Quotation, Shipment
from .models import CompanyBranch, User, UserAccess

CATEGORIES = ["SALESPERSON", "COORDINATOR", "SALES_MANAGER", "BRANCH_MANAGER", "NATIONAL_HEAD", "OPERATIONS", "ACCOUNTS", "EXECUTIVE"]
VERTICALS = ["BUSINESS", "FREIGHT_CUSTOMS", "TRANSPORT"]
DASHBOARDS = ["EXISTING", "CEO", "SALES", "COORDINATOR", "SALES_MANAGER", "BRANCH_MANAGER", "NATIONAL_SALES", "OPERATIONS", "ACCOUNTS"]
TEAM_CATEGORIES = {"COORDINATOR", "SALES_MANAGER"}
BRANCH_CATEGORIES = {"BRANCH_MANAGER", "OPERATIONS", "ACCOUNTS"}


@dataclass
class Scope:
    unrestricted: bool = True
    category: str | None = None
    branch_ids: set[int] | None = None          # None = every branch
    user_ids: set[int] | None = None            # None = everyone's records; otherwise only records of these users
    salesperson_names: set[str] = field(default_factory=set)

    def allows_branch(self, branch_id: int | None) -> bool:
        return self.branch_ids is None or (branch_id is not None and branch_id in self.branch_ids)

    def allows_user(self, user_id: int | None) -> bool:
        return self.user_ids is None or (user_id is not None and user_id in self.user_ids)

    def summary(self) -> dict:
        return {"unrestricted": self.unrestricted, "category": self.category,
                "branch_ids": None if self.branch_ids is None else sorted(self.branch_ids),
                "user_ids": None if self.user_ids is None else sorted(self.user_ids)}


def team_of(db: Session, organization_id: int, manager_id: int) -> set[int]:
    """The manager plus everyone below them in the reporting line (cycle-safe)."""
    rows = db.execute(select(UserAccess.user_id, UserAccess.reporting_manager_id).where(UserAccess.organization_id == organization_id)).all()
    children: dict[int, list[int]] = {}
    for uid, mgr in rows:
        if mgr is not None:
            children.setdefault(mgr, []).append(uid)
    seen, stack = {manager_id}, [manager_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def scope_for(db: Session, user: User) -> Scope:
    if user.is_super_admin or user.organization_id is None:
        return Scope()
    row = db.get(UserAccess, user.id)
    if row is None or not row.user_category or row.user_category == "EXECUTIVE":
        return Scope(category=row.user_category if row else None)
    branches = {b for b in [row.branch_id, *(row.extra_branch_ids or [])] if b}
    cat = row.user_category
    if cat == "NATIONAL_HEAD" and row.region:
        branches |= set(db.scalars(select(CompanyBranch.id).where(CompanyBranch.organization_id == user.organization_id, CompanyBranch.region == row.region)))
    scope = Scope(unrestricted=False, category=cat, branch_ids=branches)
    if cat == "SALESPERSON":
        scope.user_ids = {user.id}
    elif cat in TEAM_CATEGORIES:
        scope.user_ids = team_of(db, user.organization_id, user.id)
    if scope.user_ids is not None:
        for access in db.scalars(select(UserAccess).where(UserAccess.user_id.in_(scope.user_ids))):
            if access.salesperson_name:
                scope.salesperson_names.add(access.salesperson_name)
    return scope


# ---------------------------------------------------------------- record filters (None = no restriction)
from sqlalchemy import false, func, or_  # noqa: E402

from .models import Customer, CustomerAssignment, Shipment  # noqa: E402


def _assigned(user_ids):
    return select(CustomerAssignment.customer_id).where(CustomerAssignment.user_id.in_(user_ids))


def customer_clause(db: Session, user: User, scope: Scope | None = None):
    """Customers a placed user may see: salesperson / team = the customers assigned to them or created by them. Branch roles see the company's customers (branch sharing arrives with the approval workflow)."""
    scope = scope or scope_for(db, user)
    if scope.unrestricted or scope.user_ids is None:
        return None
    return or_(Customer.id.in_(_assigned(scope.user_ids)), Customer.created_by.in_(scope.user_ids))


def shipment_clause(db: Session, user: User, scope: Scope | None = None):
    """Jobs a placed user may see. Salesperson / team: jobs they or their team created, jobs sold under their salesperson name, jobs of customers assigned to them.
    Branch roles: jobs handled by their branches (branch name or code on the job) or created by people placed in those branches, plus their own."""
    scope = scope or scope_for(db, user)
    if scope.unrestricted:
        return None
    conds = [Shipment.created_by == user.id]
    if scope.user_ids is not None:
        conds += [Shipment.created_by.in_(scope.user_ids), Shipment.customer_id.in_(_assigned(scope.user_ids))]
        if scope.salesperson_names:
            conds.append(func.lower(Shipment.salesperson).in_([n.lower() for n in scope.salesperson_names]))
    else:
        branches = list(scope.branch_ids or [])
        if not branches:
            return false() if not conds else or_(*conds)
        labels = []
        for code, name in _db_rows(db, branches):
            labels += [code.lower(), name.lower()]
        conds += [func.lower(Shipment.branch).in_(labels), Shipment.created_by.in_(select(UserAccess.user_id).where(UserAccess.branch_id.in_(branches)))]
    return or_(*conds)


def _db_rows(db: Session, branch_ids):
    return db.execute(select(CompanyBranch.code, CompanyBranch.name).where(CompanyBranch.id.in_(branch_ids))).all()


def job_numbers_clause(db: Session, user: User, scope: Scope | None = None):
    """A sub-select of the job numbers this user may see (None = all)."""
    clause = shipment_clause(db, user, scope)
    return None if clause is None else select(Shipment.job_number).where(Shipment.organization_id == user.organization_id, clause)


def record_clause(db: Session, user: User, model, scope: Scope | None = None):
    """Quotations, bookings, CRM records, invoices, vouchers and payments: what the user made or their team made, the records of customers assigned to them,
    and everything tied to a job they may see. None = no restriction."""
    scope = scope or scope_for(db, user)
    if scope.unrestricted:
        return None
    conds = [model.created_by == user.id]
    if scope.user_ids is not None:
        conds.append(model.created_by.in_(scope.user_ids))
        if hasattr(model, "customer_id"):
            conds.append(model.customer_id.in_(_assigned(scope.user_ids)))
        if hasattr(model, "party_name"):
            visible = customer_clause(db, user, scope)
            conds.append(func.lower(model.party_name).in_(select(func.lower(Customer.name)).where(Customer.organization_id == user.organization_id, visible)))
    else:
        branches = list(scope.branch_ids or [])
        if branches:
            conds.append(model.created_by.in_(select(UserAccess.user_id).where(UserAccess.branch_id.in_(branches))))
    jobs = job_numbers_clause(db, user, scope)
    if jobs is not None and hasattr(model, "job_number"):
        conds.append(model.job_number.in_(jobs))
    return or_(*conds)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


_bearer = HTTPBearer(auto_error=False)
_BY_ID = {"quote_id": Quotation, "booking_id": Booking, "doc_id": Document, "payment_id": Payment, "vid": AccVoucher}


def access_guard(request: Request, credentials=Depends(_bearer), db: Session = Depends(get_db)):
    """Runs on every request: a placed user who opens a job, quotation, booking, CRM record, invoice, voucher or payment of someone else gets 404, whatever screen or sub-page asks."""
    pp = request.path_params
    if not credentials or not (pp.keys() & {"job_number", "shipment_id", "ref", *_BY_ID}):
        return
    try:
        from .security import decode_token
        uid = int(decode_token(credentials.credentials)["sub"])
    except Exception:
        return
    if not db.get(UserAccess, uid):
        return                                     # not placed: unrestricted, as before
    user = db.get(User, uid)
    if not user or not user.organization_id:
        return
    scope = scope_for(db, user)
    if scope.unrestricted:
        return
    org = user.organization_id

    def deny():
        raise HTTPException(status_code=404, detail="Not found")
    if "job_number" in pp or "shipment_id" in pp:
        clause = shipment_clause(db, user, scope)
        q = select(Shipment.id).where(Shipment.organization_id == org)
        q = q.where(Shipment.job_number == pp["job_number"]) if "job_number" in pp else q.where(Shipment.id == _int(pp["shipment_id"]))
        if db.scalar(q) is not None and db.scalar(q.where(clause)) is None:
            deny()
    for key, model in _BY_ID.items():
        if key in pp:
            clause = record_clause(db, user, model, scope)
            q = select(model.id).where(model.id == _int(pp[key]), model.organization_id == org)
            if db.scalar(q) is not None and db.scalar(q.where(clause)) is None:
                deny()
    if "ref" in pp and request.url.path.startswith("/api/crm/"):
        clause = record_clause(db, user, CrmRecord, scope)
        q = select(CrmRecord.id).where(CrmRecord.organization_id == org, CrmRecord.ref == pp["ref"])
        if db.scalar(q) is not None and db.scalar(q.where(clause)) is None:
            deny()
