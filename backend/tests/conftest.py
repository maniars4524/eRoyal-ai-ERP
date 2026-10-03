import os

os.environ.setdefault("ENV_FILE", ".env.test")

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.licensing import next_license_number
from app.models import AuditLog, Customer, MasterRecord, Organization, Permission, Role, User
from app.security import hash_password
from app.seed import PERMISSIONS

PASSWORD = "Portal-Test-Passw0rd!"
CUSTOMER_PERMS = ("customer.read", "customer.create", "customer.update", "customer.delete", "organization.read", "master.read", "master.create", "master.update", "master.delete")


@pytest.fixture(scope="module")
def world():
    try:
        db = SessionLocal()
        db.execute(select(1))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable")
    tag = uuid.uuid4().hex[:8]
    existing = {p.code for p in db.scalars(select(Permission))}
    for code, name, module, action in PERMISSIONS:
        if code not in existing:
            db.add(Permission(code=code, name=name, module=module, action=action))
    db.commit()
    perms = list(db.scalars(select(Permission).where(Permission.code.in_(CUSTOMER_PERMS))))
    platform = db.scalar(select(Organization).where(Organization.code == "RTS-PLATFORM"))
    if not platform:
        platform = Organization(code="RTS-PLATFORM", license_number=next_license_number(db), name="Platform", status="ACTIVE")
        db.add(platform)
        db.commit()
    made_orgs, made_users = [], []

    def org(is_demo):
        o = Organization(code=f"T{tag}{int(is_demo)}"[:20], license_number=next_license_number(db), name=f"Test {tag} {is_demo}", status="ACTIVE", is_demo=is_demo)
        db.add(o)
        db.flush()
        made_orgs.append(o.id)
        return o

    def user(name, organization=None, super_admin=False):
        u = User(email=f"{name}-{tag}@portal.test", username=f"{name}{tag}", password_hash=hash_password(PASSWORD), first_name=name, organization_id=organization.id if organization else None, is_super_admin=super_admin, status="ACTIVE")
        if organization:
            role = Role(name=f"TESTER-{name}", organization_id=organization.id, permissions=perms)
            u.roles.append(role)
        db.add(u)
        db.flush()
        made_users.append(u.id)
        return u

    erp_org, demo_org = org(False), org(True)
    data = {
        "platform_license": platform.license_number,
        "erp_license": erp_org.license_number,
        "dev": user("dev", super_admin=True),
        "erp": user("erp", erp_org),
        "demo": user("demo", demo_org),
        "lock": user("lock", erp_org),
        "erp_org": erp_org.id,
        "demo_org": demo_org.id,
    }
    db.commit()
    data = {k: (v.username if isinstance(v, User) else v) for k, v in data.items()}
    yield data
    db.rollback()
    db.execute(delete(AuditLog).where(AuditLog.user_id.in_(made_users)))
    db.execute(delete(MasterRecord).where(MasterRecord.organization_id.in_(made_orgs)))
    db.execute(delete(Customer).where(Customer.organization_id.in_(made_orgs)))
    db.execute(delete(User).where(User.id.in_(made_users)))
    db.execute(delete(Organization).where(Organization.id.in_(made_orgs)))
    db.commit()
    db.close()


from app.roles import ensure_role  # noqa: E402


@pytest.fixture(scope="module")
def admin(world):
    """An ERP_ADMIN (all tenant permissions) inside the world's ERP tenant."""
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    user = User(email=f"admin-{tag}@users.test", username=f"adm{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name="Adm", organization_id=org.id, status="ACTIVE")
    user.roles.append(ensure_role(db, org.id, "ERP_ADMIN"))
    db.add(user)
    db.commit()
    username, uid, license_number = user.username, user.id, org.license_number
    db.close()
    yield {"username": username, "license": license_number, "id": uid}
    db = SessionLocal()
    db.execute(delete(AuditLog).where(AuditLog.user_id == uid))
    db.commit()
    db.close()


