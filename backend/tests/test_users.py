import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Organization, User
from app.roles import ensure_role
from app.security import hash_password

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"
NEW_PASSWORD = "Brand-New-Passw0rd!!"


def login(username, portal="erp", license=None, password=PASSWORD):
    body = {"identifier": username, "password": password, "portal": portal}
    if license:
        body["license_number"] = license
    return client.post("/api/auth/login", json=body)


def hdr(username, license, password=PASSWORD):
    r = login(username, "erp", license, password)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def new_user(h, name, role="OPERATIONS", **extra):
    body = {"username": name, "email": f"{name}@users.test", "password": PASSWORD, "first_name": name.title(), "role": role, **extra}
    return client.post("/api/users", json=body, headers=h)


def test_admin_creates_user_who_can_log_in(world, admin):
    h = hdr(admin["username"], admin["license"])
    tag = uuid.uuid4().hex[:6]
    made = new_user(h, f"ops{tag}", "OPERATIONS", profile={"designation": "Executive", "department": "Ops"})
    assert made.status_code == 201, made.text
    assert made.json()["role_labels"] == ["Operations"] and made.json()["profile"]["designation"] == "Executive"
    assert login(f"ops{tag}", "erp", admin["license"]).status_code == 200
    assert f"ops{tag}" in [u["username"] for u in client.get("/api/users", headers=h).json()]


def test_role_templates_are_enforced_on_the_server(world, admin):
    h = hdr(admin["username"], admin["license"])
    tag = uuid.uuid4().hex[:6]
    new_user(h, f"vw{tag}", "VIEWER")
    new_user(h, f"op{tag}", "OPERATIONS")
    viewer = hdr(f"vw{tag}", admin["license"])
    ops = hdr(f"op{tag}", admin["license"])
    assert client.post("/api/customers", json={"code": f"V{tag}", "name": "x"}, headers=viewer).status_code == 403
    assert client.get("/api/customers", headers=viewer).status_code == 200
    made = client.post("/api/customers", json={"code": f"O{tag}", "name": "Ops customer"}, headers=ops)
    assert made.status_code == 201
    assert client.delete(f"/api/customers/{made.json()['id']}", headers=ops).status_code == 403
    assert new_user(ops, f"x{tag}").status_code == 403
    assert client.post("/api/masters/currency", json={"code": "ZZ", "name": "n"}, headers=ops).status_code == 403


def test_password_policy_and_duplicates(world, admin):
    h = hdr(admin["username"], admin["license"])
    tag = uuid.uuid4().hex[:6]
    assert client.post("/api/users", json={"username": f"p{tag}", "email": f"p{tag}@u.test", "password": "", "first_name": "P"}, headers=h).status_code == 422      # a password is needed, its length is the user's choice
    assert client.post("/api/users", json={"username": f"p{tag}", "email": "not-an-email", "password": PASSWORD, "first_name": "P"}, headers=h).status_code == 422
    assert new_user(h, f"dup{tag}").status_code == 201
    assert new_user(h, f"dup{tag}").status_code == 409
    assert new_user(h, f"badrole{tag}", "SUPERUSER").status_code == 422


def test_deactivate_reset_password_and_self_protection(world, admin):
    h = hdr(admin["username"], admin["license"])
    tag = uuid.uuid4().hex[:6]
    uid = new_user(h, f"tmp{tag}").json()["id"]
    assert client.patch(f"/api/users/{uid}", json={"status": "INACTIVE"}, headers=h).status_code == 200
    assert login(f"tmp{tag}", "erp", admin["license"]).status_code == 401
    assert client.patch(f"/api/users/{uid}", json={"status": "ACTIVE", "new_password": NEW_PASSWORD}, headers=h).status_code == 200
    assert login(f"tmp{tag}", "erp", admin["license"], PASSWORD).status_code == 401
    assert login(f"tmp{tag}", "erp", admin["license"], NEW_PASSWORD).status_code == 200
    assert client.patch(f"/api/users/{admin['id']}", json={"status": "INACTIVE"}, headers=h).status_code == 422
    assert client.patch(f"/api/users/{admin['id']}", json={"role": "VIEWER"}, headers=h).status_code == 422


def test_users_are_tenant_isolated_and_super_admin_hidden(world, admin):
    h = hdr(admin["username"], admin["license"])
    listed = client.get("/api/users", headers=h).json()
    assert all(u["username"] != world["dev"] for u in listed)
    h_demo = {"Authorization": "Bearer " + login(world["demo"], "demo").json()["access_token"]}
    assert client.get(f"/api/users/{admin['id']}", headers=h_demo).status_code in (403, 404)
    assert client.patch(f"/api/users/{admin['id']}", json={"first_name": "X"}, headers=h_demo).status_code in (403, 404)
    dev = {"Authorization": "Bearer " + login(world["dev"], "developer").json()["access_token"]}
    assert client.get("/api/users", headers=dev).status_code == 403
    assert client.get("/api/users").status_code == 401


def test_demo_user_cannot_create_or_edit_users(world):
    h_demo = {"Authorization": "Bearer " + login(world["demo"], "demo").json()["access_token"]}
    assert new_user(h_demo, "demohack").status_code == 403


def test_the_user_chooses_the_password_length(world, admin):
    h = hdr(admin["username"], admin["license"])
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/users", json={"username": f"sp{tag}", "email": f"sp{tag}@u.test", "password": "abc", "first_name": "S"}, headers=h)
    assert r.status_code == 201, r.text
    ok = client.post("/api/auth/login", json={"identifier": f"sp{tag}", "password": "abc", "portal": "erp", "license_number": admin["license"]})
    assert ok.status_code == 200
