from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Permission, Role

# Role templates available to tenant administrators. Each tenant gets its own Role row per template.
ROLE_LABELS = {"ERP_ADMIN": "Admin", "OPERATIONS": "Operations", "ACCOUNTS": "Accounts", "VIEWER": "Viewer"}
ROLE_PERMISSIONS = {
    "ERP_ADMIN": "ALL",
    "OPERATIONS": ["organization.read", "customer.read", "customer.create", "customer.update", "shipment.read", "shipment.create", "shipment.update", "master.read", "billing.read", "billing.create", "quote.read", "quote.create", "quote.send", "quote.view_cost", "booking.read", "booking.create", "booking.confirm", "credit.read", "credit.request", "job.view_history", "job.change_stage", "job.close"],
    "ACCOUNTS": ["organization.read", "customer.read", "shipment.read", "master.read", "master.create", "master.update", "billing.read", "billing.create", "billing.post", "billing.cancel", "payment.read", "payment.create", "payment.cancel", "quote.read", "quote.view_cost", "quote.rate_override", "booking.read", "credit.read", "credit.request", "job.view_history", "job.export_history", "job.reopen"],
    "VIEWER": ["organization.read", "customer.read", "shipment.read", "master.read", "user.read", "billing.read", "payment.read", "quote.read", "booking.read", "credit.read", "job.view_history"],
}
PLATFORM_ONLY = {"organization.create", "organization.activate"}


def sync_template_roles(db: Session) -> int:
    """Bring every tenant's template roles (Admin, Operations, ...) up to date after new permissions were added."""
    changed = 0
    all_permissions = [p for p in db.scalars(select(Permission)) if p.code not in PLATFORM_ONLY]
    for role in db.scalars(select(Role).options(selectinload(Role.permissions)).where(Role.organization_id.is_not(None), Role.name.in_(list(ROLE_PERMISSIONS)))):
        spec = ROLE_PERMISSIONS[role.name]
        wanted = all_permissions if spec == "ALL" else [p for p in all_permissions if p.code in spec]
        if {p.code for p in role.permissions} != {p.code for p in wanted}:
            role.permissions = wanted
            changed += 1
    db.commit()
    return changed


def ensure_role(db: Session, organization_id: int, key: str) -> Role:
    """Get or create the tenant's role for a template and keep its permissions in sync with the template."""
    if key not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=422, detail="Unknown role")
    role = db.scalar(select(Role).options(selectinload(Role.permissions)).where(Role.organization_id == organization_id, Role.name == key))
    spec = ROLE_PERMISSIONS[key]
    query = select(Permission) if spec == "ALL" else select(Permission).where(Permission.code.in_(spec))
    permissions = [p for p in db.scalars(query) if p.code not in PLATFORM_ONLY]
    if not role:
        role = Role(name=key, organization_id=organization_id, description=ROLE_LABELS[key], status="ACTIVE")
        db.add(role)
    role.permissions = permissions
    db.flush()
    return role
