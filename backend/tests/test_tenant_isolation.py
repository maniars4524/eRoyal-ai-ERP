import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.licensing import next_license_number
from app.main import app
from app.models import Organization, Permission, User
from app.security import hash_password
from app.seed import PERMISSIONS

client = TestClient(app)


def _require_database():
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable; tenant isolation requires a real database")


def _ensure_permissions_seeded(db):
    codes = {code for code, *_ in PERMISSIONS}
    existing = {p.code for p in db.scalars(select(Permission).where(Permission.code.in_(codes)))}
    for code, name, module, action in PERMISSIONS:
        if code not in existing:
            db.add(Permission(code=code, name=name, module=module, action=action))
    db.commit()


def _make_super_admin(db, suffix):
    email = f"tenant-test-super-{suffix}@esway.local"
    password = "Sup3r-Admin-Test-Pass!"
    user = User(email=email, username=f"super{suffix}", password_hash=hash_password(password), first_name="Test", last_name="SuperAdmin", is_super_admin=True)
    db.add(user)
    db.commit()
    return email, password


def _platform_license_number(db):
    platform = db.scalar(select(Organization).where(Organization.code == "RTS-PLATFORM"))
    if not platform:
        platform = Organization(code="RTS-PLATFORM", license_number=next_license_number(db), name="eSway", legal_name="eSway", country="India", status="ACTIVE")
        db.add(platform)
        db.commit()
    return platform.license_number


def _login(identifier, password, license_number):
    response = client.post("/api/auth/login", json={"identifier": identifier, "password": password, "license_number": license_number})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _create_tenant(super_token, label):
    payload = {
        "code": f"T-{label}",
        "name": f"Tenant {label}",
        "legal_name": f"Tenant {label} Ltd",
        "country": "India",
        "admin_email": f"admin-{label}@tenant.local",
        "admin_username": f"admin{label}",
        "admin_password": "Tenant-Admin-Pass-1!",
        "admin_first_name": "Admin",
        "admin_last_name": label,
    }
    response = client.post("/api/organizations", json=payload, headers={"Authorization": f"Bearer {super_token}"})
    assert response.status_code == 201, response.text
    organization = response.json()["organization"]
    return organization["id"], payload["admin_username"], payload["admin_password"], organization["license_number"]


def test_tenant_admin_cannot_read_update_or_list_other_tenants():
    _require_database()
    suffix = uuid.uuid4().hex[:8]

    db = SessionLocal()
    try:
        _ensure_permissions_seeded(db)
        super_email, super_password = _make_super_admin(db, suffix)
        platform_license = _platform_license_number(db)
    finally:
        db.close()

    super_token = _login(super_email, super_password, platform_license)

    org_a_id, admin_a_username, admin_a_password, license_a = _create_tenant(super_token, f"A{suffix}")
    org_b_id, admin_b_username, admin_b_password, license_b = _create_tenant(super_token, f"B{suffix}")

    token_a = _login(admin_a_username, admin_a_password, license_a)
    token_b = _login(admin_b_username, admin_b_password, license_b)

    own_read = client.get(f"/api/organizations/{org_a_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert own_read.status_code == 200
    assert own_read.json()["id"] == org_a_id

    cross_read_a_to_b = client.get(f"/api/organizations/{org_b_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert cross_read_a_to_b.status_code == 403

    cross_read_b_to_a = client.get(f"/api/organizations/{org_a_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert cross_read_b_to_a.status_code == 403

    cross_update_a_to_b = client.patch(
        f"/api/organizations/{org_b_id}",
        json={"legal_name": "Hijacked"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert cross_update_a_to_b.status_code == 403

    tenant_list_for_a = client.get("/api/organizations", headers={"Authorization": f"Bearer {token_a}"})
    assert tenant_list_for_a.status_code == 200
    visible_ids = {organization["id"] for organization in tenant_list_for_a.json()}
    assert visible_ids == {org_a_id}

    super_can_read_both = [
        client.get(f"/api/organizations/{org_id}", headers={"Authorization": f"Bearer {super_token}"}).status_code
        for org_id in (org_a_id, org_b_id)
    ]
    assert super_can_read_both == [200, 200]
