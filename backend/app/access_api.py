"""Company branches, user placement (branch / category / reporting line), customer assignments and the dashboards a user may open."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import CATEGORIES, DASHBOARDS, VERTICALS, scope_for, team_of
from .auth import get_current_user, has_permission, permission_required
from .auth_api import record_audit
from .db import get_db
from .models import CompanyBranch, Customer, CustomerAssignment, User, UserAccess

router = APIRouter(prefix="/api/access", tags=["access"])


def _org(user: User) -> int:
    if user.organization_id is None:
        raise HTTPException(403, "Access settings are company-scoped")
    return user.organization_id


def branch_dict(b: CompanyBranch) -> dict:
    return {"id": b.id, "code": b.code, "name": b.name, "region": b.region, "city": b.city, "state": b.state, "is_head_office": b.is_head_office, "status": b.status}


def access_dict(a: UserAccess | None) -> dict:
    if a is None:
        return {"user_category": None, "branch_id": None, "extra_branch_ids": [], "region": None, "designation": None, "reporting_manager_id": None,
                "business_vertical": None, "job_categories": [], "salesperson_name": None, "dashboards": []}
    return {"user_category": a.user_category, "branch_id": a.branch_id, "extra_branch_ids": a.extra_branch_ids or [], "region": a.region, "designation": a.designation,
            "reporting_manager_id": a.reporting_manager_id, "business_vertical": a.business_vertical, "job_categories": a.job_categories or [],
            "salesperson_name": a.salesperson_name, "dashboards": a.dashboards or []}


def allowed_dashboards(db: Session, user: User) -> list[str]:
    """The Existing dashboard is always there; the others only when granted. CEO needs dashboard.ceo (the platform super admin has it implicitly)."""
    a = db.get(UserAccess, user.id)
    out = ["EXISTING"] + [d for d in (a.dashboards if a and a.dashboards else []) if d in DASHBOARDS and d not in ("EXISTING", "CEO")]
    if has_permission(user, "dashboard.ceo"):
        out.append("CEO")
    return out


@router.get("/me")
def my_access(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    scope = scope_for(db, user)
    return {"access": access_dict(db.get(UserAccess, user.id)), "scope": scope.summary(), "dashboards": allowed_dashboards(db, user), "ceo_dashboard": has_permission(user, "dashboard.ceo")}


@router.get("/options")
def options(user: User = Depends(get_current_user)):
    return {"categories": CATEGORIES, "verticals": VERTICALS, "dashboards": DASHBOARDS}


# ------------------------------------------------------------------ branches
class BranchIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=60)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    is_head_office: bool = False
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")


@router.get("/branches")
def list_branches(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(CompanyBranch).where(CompanyBranch.organization_id == _org(user)).order_by(CompanyBranch.name)
    return [branch_dict(b) for b in db.scalars(q)]


@router.post("/branches", status_code=201)
def create_branch(body: BranchIn, user: User = Depends(permission_required("branch.manage")), db: Session = Depends(get_db)):
    org = _org(user)
    code = body.code.strip().upper()
    if db.scalar(select(CompanyBranch).where(CompanyBranch.organization_id == org, CompanyBranch.code == code)):
        raise HTTPException(409, f"A branch with code {code} already exists")
    b = CompanyBranch(organization_id=org, code=code, name=body.name.strip(), region=body.region, city=body.city, state=body.state, is_head_office=body.is_head_office, status=body.status, created_by=user.id)
    db.add(b)
    db.flush()
    record_audit(db, "BRANCH_CREATED", "ACCESS", user, org, "company_branch", str(b.id), new_value=branch_dict(b))
    db.commit()
    return branch_dict(b)


@router.put("/branches/{branch_id}")
def update_branch(branch_id: int, body: BranchIn, user: User = Depends(permission_required("branch.manage")), db: Session = Depends(get_db)):
    org = _org(user)
    b = db.get(CompanyBranch, branch_id)
    if not b or b.organization_id != org:
        raise HTTPException(404, "Branch not found")
    code = body.code.strip().upper()
    if code != b.code and db.scalar(select(CompanyBranch).where(CompanyBranch.organization_id == org, CompanyBranch.code == code)):
        raise HTTPException(409, f"A branch with code {code} already exists")
    old = branch_dict(b)
    b.code, b.name, b.region, b.city, b.state, b.is_head_office, b.status = code, body.name.strip(), body.region, body.city, body.state, body.is_head_office, body.status
    record_audit(db, "BRANCH_UPDATED", "ACCESS", user, org, "company_branch", str(b.id), new_value=branch_dict(b), old_value=old)
    db.commit()
    return branch_dict(b)


# ------------------------------------------------------------------ user placement
class AccessIn(BaseModel):
    user_category: str | None = None
    branch_id: int | None = None
    extra_branch_ids: list[int] = []
    region: str | None = Field(default=None, max_length=60)
    designation: str | None = Field(default=None, max_length=80)
    reporting_manager_id: int | None = None
    business_vertical: str | None = None
    job_categories: list[str] = []
    salesperson_name: str | None = Field(default=None, max_length=120)
    dashboards: list[str] = []


@router.get("/users")
def list_user_access(user: User = Depends(permission_required("access.manage")), db: Session = Depends(get_db)):
    org = _org(user)
    rows = {a.user_id: a for a in db.scalars(select(UserAccess).where(UserAccess.organization_id == org))}
    users = db.scalars(select(User).where(User.organization_id == org, User.is_super_admin.is_(False)).order_by(User.first_name))
    return [{"id": u.id, "name": f"{u.first_name} {u.last_name}".strip(), "username": u.username, "status": u.status, **access_dict(rows.get(u.id))} for u in users]


@router.put("/users/{user_id}")
def set_user_access(user_id: int, body: AccessIn, user: User = Depends(permission_required("access.manage")), db: Session = Depends(get_db)):
    org = _org(user)
    target = db.get(User, user_id)
    if not target or target.organization_id != org:
        raise HTTPException(404, "User not found")
    if body.user_category and body.user_category not in CATEGORIES:
        raise HTTPException(422, "Unknown user category")
    if body.business_vertical and body.business_vertical not in VERTICALS:
        raise HTTPException(422, "Unknown business vertical")
    if any(d not in DASHBOARDS for d in body.dashboards):
        raise HTTPException(422, "Unknown dashboard")
    if "CEO" in body.dashboards:
        raise HTTPException(422, "The CEO dashboard is granted with the dashboard.ceo permission, not here")
    wanted = {b for b in [body.branch_id, *body.extra_branch_ids] if b}
    found = set(db.scalars(select(CompanyBranch.id).where(CompanyBranch.organization_id == org, CompanyBranch.id.in_(wanted)))) if wanted else set()
    if wanted - found:
        raise HTTPException(422, "A selected branch does not belong to this company")
    if body.reporting_manager_id:
        mgr = db.get(User, body.reporting_manager_id)
        if not mgr or mgr.organization_id != org or mgr.id == target.id:
            raise HTTPException(422, "Invalid reporting manager")
        if body.reporting_manager_id in team_of(db, org, target.id):
            raise HTTPException(422, "The reporting line would loop back on itself")
    if body.user_category and body.user_category != "EXECUTIVE" and not body.branch_id and not body.extra_branch_ids and not body.region:
        raise HTTPException(422, "Choose a branch (or a region) for this category, otherwise the user would see nothing")
    row = db.get(UserAccess, user_id)
    old = access_dict(row)
    if row is None:
        row = UserAccess(user_id=user_id, organization_id=org)
        db.add(row)
    row.user_category, row.branch_id, row.extra_branch_ids, row.region = body.user_category, body.branch_id, body.extra_branch_ids, body.region
    row.designation, row.reporting_manager_id, row.business_vertical = body.designation, body.reporting_manager_id, body.business_vertical
    row.job_categories, row.salesperson_name, row.dashboards, row.updated_by = body.job_categories, (body.salesperson_name or None), body.dashboards, user.id
    db.flush()
    record_audit(db, "USER_ACCESS_CHANGED", "ACCESS", user, org, "user", str(user_id), new_value=access_dict(row), old_value=old)
    db.commit()
    return access_dict(row)


# ------------------------------------------------------------------ customer assignments
class AssignIn(BaseModel):
    user_ids: list[int]


@router.get("/customers/{customer_id}/assignments")
def get_assignments(customer_id: int, user: User = Depends(permission_required("access.manage")), db: Session = Depends(get_db)):
    c = db.get(Customer, customer_id)
    if not c or c.organization_id != _org(user):
        raise HTTPException(404, "Customer not found")
    return {"customer_id": c.id, "user_ids": list(db.scalars(select(CustomerAssignment.user_id).where(CustomerAssignment.customer_id == c.id)))}


@router.put("/customers/{customer_id}/assignments")
def set_assignments(customer_id: int, body: AssignIn, user: User = Depends(permission_required("access.manage")), db: Session = Depends(get_db)):
    org = _org(user)
    c = db.get(Customer, customer_id)
    if not c or c.organization_id != org:
        raise HTTPException(404, "Customer not found")
    ids = set(body.user_ids)
    valid = set(db.scalars(select(User.id).where(User.organization_id == org, User.id.in_(ids)))) if ids else set()
    if ids - valid:
        raise HTTPException(422, "A selected user does not belong to this company")
    current = {a.user_id: a for a in db.scalars(select(CustomerAssignment).where(CustomerAssignment.customer_id == c.id))}
    for uid, a in current.items():
        if uid not in ids:
            db.delete(a)
    for uid in ids - set(current):
        db.add(CustomerAssignment(organization_id=org, customer_id=c.id, user_id=uid, assigned_by=user.id))
    record_audit(db, "CUSTOMER_ASSIGNED", "ACCESS", user, org, "customer", str(c.id), new_value={"user_ids": sorted(ids)}, old_value={"user_ids": sorted(current)})
    db.commit()
    return {"customer_id": c.id, "user_ids": sorted(ids)}
