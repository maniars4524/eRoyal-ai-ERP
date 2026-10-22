import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_standard_airline_file_skips_placeholder_and_repeats_and_never_duplicates(h):
    pre = client.post("/api/network/airlines/import-standard", json={}, headers=h).json()
    assert pre["applied"] is False and pre["skipped"] == 3 and pre["errors"] == 0 and pre["new"] == 94
    assert client.get("/api/network/airlines", headers=h).json()["total"] == 0
    assert client.post("/api/network/airlines/import-standard", json={"apply": True}, headers=h).json()["applied"] is True
    again = client.post("/api/network/airlines/import-standard", json={"apply": True}, headers=h).json()
    assert again["new"] == 0
    lst = client.get("/api/network/airlines?q=air india&limit=50", headers=h).json()
    assert lst["total"] >= 1 and all(len(a["num_code"] or "000") == 3 for a in lst["items"])


def test_organization_roles_create_and_link_masters(h):
    r = client.post("/api/customers", json={"code": "NET-AL1", "name": "Zeta Airways Net", "roles": ["carrier"], "data": {"carrier_airline": "yes", "airline_code": "ZZ", "iata_numeric_code": "55"}}, headers=h)
    assert r.status_code == 201, r.text
    a = client.get("/api/network/airlines?q=Zeta", headers=h).json()["items"]
    assert len(a) == 1 and a[0]["code"] == "ZZ" and a[0]["num_code"] == "055" and a[0]["customer_id"] == r.json()["id"]
    dup = client.post("/api/customers", json={"code": "NET-AL2", "name": "Other Name", "roles": ["carrier"], "data": {"carrier_airline": "yes", "airline_code": "ZZ"}}, headers=h)
    assert dup.status_code == 409
    assert client.post("/api/customers", json={"code": "NET-IA", "name": "No Code Agent", "roles": ["iata"], "data": {}}, headers=h).status_code == 422
    assert client.delete(f"/api/customers/{r.json()['id']}", headers=h).status_code == 409
    client.patch(f"/api/customers/{r.json()['id']}", json={"roles": ["shipper"]}, headers=h)
    assert client.get("/api/network/airlines?q=Zeta", headers=h).json()["items"][0]["status"] == "INACTIVE"


def test_stock_line_validation(h):
    air = client.get("/api/network/airlines?q=Zeta", headers=h).json()["items"][0]["id"]
    ok = client.post("/api/network/stock-lines", json={"airline_id": air, "from_awb": "12345000", "to_awb": "12345099"}, headers=h)
    assert ok.status_code == 201 and ok.json()["awb_count"] == 100
    assert client.post("/api/network/stock-lines", json={"airline_id": air, "from_awb": "12345050", "to_awb": "12345200"}, headers=h).status_code == 409
    assert client.post("/api/network/stock-lines", json={"airline_id": air, "from_awb": "12345300", "to_awb": "12345200"}, headers=h).status_code == 422
