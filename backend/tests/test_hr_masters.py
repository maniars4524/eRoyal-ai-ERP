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


ROWS = [{"code": "DES001", "name": "Managing Director", "department": "Management", "level": "l1"}, {"code": "DES002", "name": "General Manager", "department": "Administration", "level": "L2"},
        {"code": "DES003", "name": "HR Manager", "department": "HR", "level": "L3"}, {"code": "DES004", "name": "HR Executive", "department": "HR", "level": "L4"},
        {"code": "DES012", "name": "Sales Executive", "department": "Sales", "level": "L4"}]


def test_nothing_is_pre_filled_and_a_list_can_be_imported_checked_first(h):
    assert client.get("/api/masters/designation", headers=h).json() == [] and client.get("/api/masters/department", headers=h).json() == []      # no made-up masters
    pre = client.post("/api/masters/designation/import", json={"rows": ROWS}, headers=h).json()
    assert pre["applied"] is False and pre["new"] == 5 and pre["skipped"] == 0 and pre["errors"] == 0 and set(pre["new_departments"]) == {"Management", "Administration", "HR", "Sales"}
    assert client.get("/api/masters/designation", headers=h).json() == []                       # checking saves nothing
    done = client.post("/api/masters/designation/import", json={"rows": ROWS, "apply": True}, headers=h).json()
    assert done["applied"] is True and done["new"] == 5
    des = {d["code"]: d for d in client.get("/api/masters/designation", headers=h).json()}
    assert len(des) == 5 and des["DES001"]["data"] == {"department": "Management", "level": "L1"} and des["DES004"]["data"]["department"] == "HR" and des["DES004"]["status"] == "ACTIVE"
    deps = {d["name"]: d for d in client.get("/api/masters/department", headers=h).json()}
    assert set(deps) == {"Management", "Administration", "HR", "Sales"} and len({d["code"] for d in deps.values()}) == 4        # each department got its own code


def test_import_skips_what_exists_and_refuses_bad_codes(h):
    again = client.post("/api/masters/designation/import", json={"rows": ROWS + [{"code": "DES099", "name": "Sales Executive", "department": "Sales", "level": "L4"}, {"code": "DES050", "name": "Storekeeper", "department": "Stores", "level": "L5"}], "apply": True}, headers=h).json()
    assert again["new"] == 1 and again["skipped"] == 6 and again["new_departments"] == ["Stores"]
    msgs = {r["code"]: r["message"] for r in again["rows"] if r["status"] == "SKIP"}
    assert msgs["DES001"] == "This code already exists" and msgs["DES099"] == "This designation already exists"
    bad = client.post("/api/masters/designation/import", json={"rows": [{"code": "BAD#1", "name": "Broken", "department": "HR"}], "apply": True}, headers=h)
    assert bad.status_code == 422
    assert len(client.get("/api/masters/designation", headers=h).json()) == 6
    assert client.post("/api/masters/designation/import", json={"rows": []}, headers=h).status_code == 422
