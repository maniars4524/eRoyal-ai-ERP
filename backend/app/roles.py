from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Permission, Role

# Role templates available to tenant administrators. Each tenant gets its own Role row per template.
ROLE_LABELS = {"ERP_ADMIN": "Admin", "OPERATIONS": "Operations", "ACCOUNTS": "Accounts", "VIEWER": "Viewer"}
ROLE_PERMISSIONS = {
    "ERP_ADMIN": "ALL",
    "OPERATIONS": ["organization.read", "customer.read", "customer.create", "customer.update", "shipment.read", "shipment.create", "shipment.update", "master.read", "billing.read", "billing.create"],
    "ACCOUNTS": ["organization.read", "customer.read", "shipment.read", "master.read", "master.create", "master.update", "billing.read", "billing.create", "billing.post", "billing.cancel", "payment.read", "payment.create", "payment.cancel"],
    "VIEWER": ["organization.read", "customer.read", "shipment.read", "master.read", "user.read", "billing.read", "payment.read"],
}
PLATFORM_ONLY = {"organization.create", "organization.activate"}


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
