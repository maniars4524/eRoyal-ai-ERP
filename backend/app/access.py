"""Who may see whose records. One function, scope_for(), answers it for every module.

A user with no placement row (or an executive / platform user) is unrestricted: the ERP behaves exactly as before for them.
Placed users are narrowed by category: salesperson = own records, coordinator / sales manager = own team (the reporting line below them),
branch manager / operations / accounts = their branches, national head = the branches and regions they were given. Deny by default when a placed user has no branch.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

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
