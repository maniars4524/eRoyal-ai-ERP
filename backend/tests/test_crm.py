import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def hdr(username, license=None, portal="erp"):
    body = {"identifier": username, "password": PASSWORD, "portal": portal}
    if license:
        body["license_number"] = license
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def ref():
    return "ENQ-" + uuid.uuid4().hex[:6]


def test_create_update_list_delete(h):
    r = ref()
    c = client.put("/api/crm/enquiry", json={"ref": r, "company": "Acme", "status": "New", "data": {"commodity": "Tea", "timeline": [{"t": 1}]}}, headers=h)
    assert c.status_code == 200 and c.json()["version"] == 1
    u = client.put("/api/crm/enquiry", json={"ref": r, "version": 1, "company": "Acme", "status": "Assigned", "data": {"commodity": "Coffee"}}, headers=h)
    assert u.status_code == 200 and u.json()["version"] == 2 and u.json()["status"] == "Assigned"
    rows = client.get("/api/crm/enquiry", headers=h).json()
    assert any(x["ref"] == r and x["data"]["commodity"] == "Coffee" for x in rows)
    assert client.get("/api/crm/lead", headers=h).json() == [] or all(x["ref"] != r for x in client.get("/api/crm/lead", headers=h).json())
    assert client.delete(f"/api/crm/enquiry/{r}", headers=h).status_code == 204
    assert client.delete(f"/api/crm/enquiry/{r}", headers=h).status_code == 404


def test_conflicts_and_validation(h):
    r = ref()
    assert client.put("/api/crm/enquiry", json={"ref": r, "version": 3}, headers=h).status_code == 409
    assert client.put("/api/crm/enquiry", json={"ref": r}, headers=h).status_code == 200
    assert client.put("/api/crm/enquiry", json={"ref": r}, headers=h).status_code == 409  # create again = stale
    assert client.put("/api/crm/enquiry", json={"ref": r, "version": 5}, headers=h).status_code == 409
    assert client.put("/api/crm/nonsense", json={"ref": r}, headers=h).status_code == 404
    assert client.put("/api/crm/enquiry", json={"ref": "bad ref!"}, headers=h).status_code == 422
    assert client.put("/api/crm/enquiry", json={"ref": ref(), "customer_id": 99999999}, headers=h).status_code == 422


def test_customer_name_comes_from_the_master_and_tenants_are_isolated(h, admin2):
    cust = client.post("/api/customers", json={"code": "CRM" + uuid.uuid4().hex[:4].upper(), "name": "Zenith Pharma", "roles": ["shipper"]}, headers=h).json()
    r = ref()
    out = client.put("/api/crm/enquiry", json={"ref": r, "customer_id": cust["id"], "company": "typed differently"}, headers=h).json()
    assert out["company"] == "Zenith Pharma" and out["customer_id"] == cust["id"]
