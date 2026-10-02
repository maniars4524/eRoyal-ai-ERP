import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, User

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def login(username, portal, license=None, password=PASSWORD):
    body = {"identifier": username, "password": password, "portal": portal}
    if license:
        body["license_number"] = license
    return client.post("/api/auth/login", json=body)


def headers(response):
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_developer_portal_only_for_super_admin(world):
    ok = login(world["dev"], "developer")
    assert ok.status_code == 200 and ok.json()["user"]["portal"] == "developer"
    assert login(world["erp"], "developer", world["erp_license"]).status_code == 403
    assert login(world["demo"], "developer").status_code == 403


def test_erp_portal_rejects_developer_and_demo_accounts(world):
    ok = login(world["erp"], "erp", world["erp_license"])
    assert ok.status_code == 200 and ok.json()["user"]["portal"] == "erp"
    wrong_dev = login(world["dev"], "erp", world["platform_license"])
    assert wrong_dev.status_code == 403 and "Developer" in wrong_dev.json()["detail"]
    assert login(world["demo"], "erp").status_code == 403


def test_erp_portal_requires_license_number(world):
    assert login(world["erp"], "erp").status_code == 401
    assert login(world["erp"], "erp", "RTS9999").status_code == 401


def test_demo_portal_only_for_demo_accounts(world):
    ok = login(world["demo"], "demo")
    assert ok.status_code == 200 and ok.json()["user"]["portal"] == "demo"
    assert login(world["erp"], "demo", world["erp_license"]).status_code == 403
    assert login(world["dev"], "demo").status_code == 403


def test_wrong_password_is_generic_401(world):
    assert login(world["erp"], "erp", world["erp_license"], password="nope").status_code == 401
    assert login("no-such-user", "erp", "RTS0001").status_code == 401


def test_demo_user_cannot_delete_or_create_orgs_but_can_create_customers(world):
    h = headers(login(world["demo"], "demo"))
    created = client.post("/api/customers", json={"code": "DX1", "name": "Demo Customer X"}, headers=h)
    assert created.status_code == 201, created.text
    deleted = client.delete(f"/api/customers/{created.json()['id']}", headers=h)
    assert deleted.status_code == 403 and "demo" in deleted.json()["detail"].lower()
    assert client.post("/api/organizations", json={}, headers=h).status_code in (403, 422)


def test_erp_user_can_delete_own_customer_and_tenants_are_isolated(world):
    h_erp = headers(login(world["erp"], "erp", world["erp_license"]))
    h_demo = headers(login(world["demo"], "demo"))
    made = client.post("/api/customers", json={"code": "EX1", "name": "ERP Customer"}, headers=h_erp)
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    assert cid not in [c["id"] for c in client.get("/api/customers", headers=h_demo).json()]
    assert client.get(f"/api/customers/{cid}", headers=h_demo).status_code in (403, 404)
    assert client.delete(f"/api/customers/{cid}", headers=h_erp).status_code in (200, 204)


def test_developer_token_cannot_use_tenant_data_apis(world):
    h = headers(login(world["dev"], "developer"))
    assert client.get("/api/shipments", headers=h).status_code == 403


def test_unauthenticated_requests_rejected():
    assert client.get("/api/customers").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_lockout_after_repeated_failures_and_audit(world):
    for _ in range(5):
        assert login(world["lock"], "erp", world["erp_license"], password="wrong").status_code == 401
    locked = login(world["lock"], "erp", world["erp_license"])
    assert locked.status_code == 429
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == world["lock"]))
        actions = set(db.scalars(select(AuditLog.action).where(AuditLog.user_id == user.id)))
    finally:
        db.close()
    assert {"LOGIN_FAILED", "ACCOUNT_LOCKED", "LOGIN_BLOCKED_LOCKED"} <= actions


def test_portal_routes_serve_ui():
    for path in ("/developer/login", "/erp/login", "/demo/login", "/developer", "/erp", "/demo"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
