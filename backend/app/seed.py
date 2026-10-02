import os

from sqlalchemy import select

from .config import IS_PRODUCTION
from .db import SessionLocal
from .licensing import next_license_number
from .models import Organization, Permission, Role, User
from .security import hash_password

PERMISSIONS = [
    ("organization.read", "Read organizations", "organization", "read"),
    ("organization.create", "Create organizations", "organization", "create"),
    ("organization.update", "Update organizations", "organization", "update"),
    ("organization.activate", "Activate organizations", "organization", "activate"),
    ("user.read", "Read users", "user", "read"),
    ("user.create", "Create users", "user", "create"),
    ("customer.read", "Read customers", "customer", "read"),
    ("customer.create", "Create customers", "customer", "create"),
    ("customer.update", "Update customers", "customer", "update"),
    ("customer.delete", "Delete customers", "customer", "delete"),
    ("shipment.read", "Read shipment jobs", "shipment", "read"),
    ("shipment.create", "Create shipment jobs", "shipment", "create"),
    ("shipment.update", "Update shipment jobs", "shipment", "update"),
    ("shipment.delete", "Delete shipment jobs", "shipment", "delete"),
]


def seed_super_admin() -> None:
    db = SessionLocal()
    try:
        permissions = {}
        for code, name, module, action in PERMISSIONS:
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                permission = Permission(code=code, name=name, module=module, action=action)
                db.add(permission)
            permissions[code] = permission
        email = os.getenv("SUPER_ADMIN_EMAIL", "admin@eroyal.local")
        username = os.getenv("SUPER_ADMIN_USERNAME", "Admin")
        password = os.getenv("SUPER_ADMIN_PASSWORD", "admin")
        if IS_PRODUCTION and (len(password) < 12 or password.lower() in {"admin", "password", "mani"}):
            raise RuntimeError("SUPER_ADMIN_PASSWORD must be at least 12 characters when APP_ENV=production")
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, username=username, password_hash=hash_password(password), first_name="Platform", last_name="Administrator", is_super_admin=True)
            db.add(user)
        platform_org = db.scalar(select(Organization).where(Organization.code == "RTS-PLATFORM"))
        if not platform_org:
            platform_org = Organization(code="RTS-PLATFORM", license_number="RTS0001", name="eSway ERP 2.0", legal_name="eSway ERP 2.0", country="India", status="ACTIVE")
            db.add(platform_org)
            db.flush()
        if IS_PRODUCTION:
            # Production gets only the platform Super Admin; tenants are created through the UI.
            db.commit()
            return
        demo_org_code = os.getenv("DEMO_ORG_CODE", "NRS-FREIGHT")
        demo_org_name = os.getenv("DEMO_ORG_NAME", "NRS FREIGHT")
        demo_org = db.scalar(select(Organization).where(Organization.code == demo_org_code))
        if not demo_org:
            demo_org = Organization(code=demo_org_code, license_number=next_license_number(db), name=demo_org_name, legal_name=demo_org_name, country="India", status="ACTIVE")
            db.add(demo_org)
            db.flush()
        erp_role = db.scalar(select(Role).where(Role.organization_id == demo_org.id, Role.name == "ERP_ADMIN"))
        if not erp_role:
            erp_role = Role(name="ERP_ADMIN", organization_id=demo_org.id, description="Demo ERP administrator", status="ACTIVE", permissions=[permissions[code] for code in ("organization.read", "organization.update", "user.read", "user.create", "customer.read", "customer.create", "customer.update", "customer.delete", "shipment.read", "shipment.create", "shipment.update", "shipment.delete")])
            db.add(erp_role)
        else:
            existing_codes = {p.code for p in erp_role.permissions}
            for code in ("customer.read", "customer.create", "customer.update", "customer.delete", "shipment.read", "shipment.create", "shipment.update", "shipment.delete"):
                if code not in existing_codes:
                    erp_role.permissions.append(permissions[code])
        demo_username = os.getenv("DEMO_USER_USERNAME", "mani")
        demo_email = os.getenv("DEMO_USER_EMAIL", "mani@eroyal.local")
        demo_user = db.scalar(select(User).where((User.username == demo_username) | (User.email == demo_email)))
        if not demo_user:
            demo_user = User(email=demo_email, username=demo_username, password_hash=hash_password(os.getenv("DEMO_USER_PASSWORD", "mani")), first_name="Mani", last_name="Kumar", organization_id=demo_org.id, status="ACTIVE")
            demo_user.roles.append(erp_role)
            db.add(demo_user)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_super_admin()
