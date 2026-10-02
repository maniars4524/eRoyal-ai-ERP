from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def auth(username, portal, license=None):
    body = {"identifier": username, "password": PASSWORD, "portal": portal}
    if license:
        body["license_number"] = license
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_master_crud_roundtrip(world):
    h = auth(world["erp"], "erp", world["erp_license"])
    created = client.post("/api/masters/currency", json={"code": "usd", "name": "US Dollar", "data": {"symbol": "$", "decimal_places": 2}}, headers=h)
    assert created.status_code == 201, created.text
    rec = created.json()
    assert rec["code"] == "USD" and rec["data"]["symbol"] == "$"
    assert client.get(f"/api/masters/currency/{rec['id']}", headers=h).json()["name"] == "US Dollar"
    upd = client.patch(f"/api/masters/currency/{rec['id']}", json={"name": "United States Dollar", "status": "INACTIVE", "data": {"symbol": "US$"}}, headers=h)
    assert upd.status_code == 200 and upd.json()["status"] == "INACTIVE" and upd.json()["data"] == {"symbol": "US$"}
    assert [r["code"] for r in client.get("/api/masters/currency?q=dollar", headers=h).json()] == ["USD"]
    assert client.get("/api/masters/currency?status=ACTIVE", headers=h).json() == []
    assert client.delete(f"/api/masters/currency/{rec['id']}", headers=h).status_code == 204
    assert client.get(f"/api/masters/currency/{rec['id']}", headers=h).status_code == 404


def test_duplicate_code_is_409_and_types_are_independent(world):
    h = auth(world["erp"], "erp", world["erp_license"])
    assert client.post("/api/masters/unit", json={"code": "KG", "name": "Kilogram"}, headers=h).status_code == 201
    assert client.post("/api/masters/unit", json={"code": "kg", "name": "Again"}, headers=h).status_code == 409
    assert client.post("/api/masters/pack", json={"code": "KG", "name": "Same code, other master"}, headers=h).status_code == 201


def test_validation_rejects_bad_input(world):
    h = auth(world["erp"], "erp", world["erp_license"])
    assert client.post("/api/masters/unit", json={"code": "", "name": "x"}, headers=h).status_code == 422
    assert client.post("/api/masters/unit", json={"code": "A;B", "name": "x"}, headers=h).status_code == 422
    assert client.post("/api/masters/unit", json={"code": "OK1", "name": "x", "data": {"Bad Key": 1}}, headers=h).status_code == 422
    assert client.post("/api/masters/unit", json={"code": "OK2", "name": "x", "data": {"nested": {"a": 1}}}, headers=h).status_code == 422
    assert client.post("/api/masters/unit", json={"code": "OK3", "name": "x", "status": "DELETED"}, headers=h).status_code == 422
    assert client.get("/api/masters/not_a_type", headers=h).status_code == 404


def test_masters_are_tenant_isolated(world):
    h_erp = auth(world["erp"], "erp", world["erp_license"])
    h_demo = auth(world["demo"], "demo")
    made = client.post("/api/masters/country", json={"code": "IN", "name": "India"}, headers=h_erp).json()
    assert client.get("/api/masters/country", headers=h_demo).json() == []
    assert client.get(f"/api/masters/country/{made['id']}", headers=h_demo).status_code == 404
    assert client.patch(f"/api/masters/country/{made['id']}", json={"name": "Hacked"}, headers=h_demo).status_code == 404
    assert client.delete(f"/api/masters/country/{made['id']}", headers=h_demo).status_code in (403, 404)
    assert client.get(f"/api/masters/country/{made['id']}", headers=h_erp).json()["name"] == "India"


def test_demo_user_cannot_delete_masters_but_can_create(world):
    h = auth(world["demo"], "demo")
    made = client.post("/api/masters/branch", json={"code": "DEMO-HQ", "name": "Demo HQ"}, headers=h)
    assert made.status_code == 201
    gone = client.delete(f"/api/masters/branch/{made.json()['id']}", headers=h)
    assert gone.status_code == 403 and "demo" in gone.json()["detail"].lower()


def test_super_admin_and_anonymous_cannot_use_masters(world):
    assert client.get("/api/masters/country").status_code == 401
    h = auth(world["dev"], "developer")
    assert client.get("/api/masters/country", headers=h).status_code == 403
