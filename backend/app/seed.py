import os

from sqlalchemy import select

from .db import SessionLocal
from .models import Organization, Permission, Role, User
from .security import hash_password

PERMISSIONS = [
    ("organization.read", "Read organizations", "organization", "read"),
    ("organization.create", "Create organizations", "organization", "create"),
    ("organization.update", "Update organizations", "organization", "update"),
    ("organization.activate", "Activate organizations", "organization", "activate"),
    ("user.read", "Read users", "user", "read"),
    ("user.create", "Create users", "user", "create"),
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
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, username=username, password_hash=hash_password(password), first_name="Platform", last_name="Administrator", is_super_admin=True)
            db.add(user)
        demo_org = db.scalar(select(Organization).where(Organization.code == "DEMO-RGC"))
        if not demo_org:
            demo_org = Organization(code="NRS-FREIGHT", name="NRS FREIGHT", legal_name="NRS FREIGHT", country="India", status="ACTIVE")
            db.add(demo_org)
            db.flush()
        erp_role = db.scalar(select(Role).where(Role.organization_id == demo_org.id, Role.name == "ERP_ADMIN"))
        if not erp_role:
            erp_role = Role(name="ERP_ADMIN", organization_id=demo_org.id, description="Demo ERP administrator", status="ACTIVE", permissions=[permissions[code] for code in ("organization.read", "organization.update", "user.read", "user.create")])
            db.add(erp_role)
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
