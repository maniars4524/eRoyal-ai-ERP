import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog

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


def test_catalogue_import_gives_every_charge_its_sac_and_activates_it(h):
    r = client.post("/api/masters/charge/catalogue/import", headers=h)
    assert r.status_code == 201 and r.json()["charges_created"] == 47 and r.json()["headings_created"] >= 3
    again = client.post("/api/masters/charge/catalogue/import", headers=h).json()
    assert again["charges_created"] == 0 and again["charges_skipped"] == 47
    charges = {c["code"]: c for c in client.get("/api/masters/charge", headers=h).json()}
    cat = {k: c for k, c in charges.items() if c["data"].get("catalogue") == "yes"}
    assert len(cat) >= 47 and all(c["status"] == "ACTIVE" and len(c["data"]["hsn_code"]) == 6 for c in cat.values())
    assert charges["SEA-FRT-DOM"]["data"]["gst_percent"] == "5" and charges["SEA-FRT-DOM"]["data"]["hsn_code"] == "996521"
    assert charges["SEA-FRT-EXP"]["data"]["tax_treatment"] == "zero_rated" and charges["SEA-FRT-IMP"]["data"]["tax_treatment"] == "exempt"
    assert charges["THC"]["data"]["hsn_code"] == "996711" and charges["DUTY-RECOVERY"]["data"]["tax_treatment"] == "non_gst"
    hsn = {x["code"] for x in client.get("/api/masters/hsn", headers=h).json()}
    assert {c["data"]["hsn_code"] for c in cat.values()} <= hsn            # every SAC used is in the HSN / SAC master
    assert len([c for c in client.get("/api/masters/charge?status=ACTIVE", headers=h).json() if c["data"].get("catalogue") == "yes"]) >= 47   # all offered in the pick-lists


def test_complete_hsn_fills_the_charges_that_have_none_and_upgrades_headings(h):
    client.post("/api/masters/charge/catalogue/import", headers=h)
    a = client.post("/api/masters/charge", json={"code": "ZZ-NOHSN", "name": "Zz Handling Extra", "status": "INACTIVE", "data": {"category": "Handling"}}, headers=h)
    b = client.post("/api/masters/charge", json={"code": "ZZ-OWN", "name": "Zz Own Code", "data": {"hsn_code": "998399", "gst_percent": "18", "category": "Other"}}, headers=h)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    thc = next(c for c in client.get("/api/masters/charge", headers=h).json() if c["code"] == "THC")
    client.patch(f"/api/masters/charge/{thc['id']}", json={"data": dict(thc["data"], hsn_code="9967")}, headers=h)   # an old heading-level code
    r = client.post("/api/masters/charge/complete-hsn", headers=h)
    assert r.status_code == 200, r.text
    done = {x["code"]: x for x in r.json()["updated"]}
    assert "ZZ-NOHSN" in done and "THC" in done and "ZZ-OWN" not in done and "SAC 996711" in " ".join(done["THC"]["changes"])
    charges = {c["code"]: c for c in client.get("/api/masters/charge", headers=h).json()}
    assert charges["ZZ-NOHSN"]["data"]["hsn_code"] == "996713" and charges["ZZ-NOHSN"]["status"] == "ACTIVE"
    assert charges["ZZ-OWN"]["data"]["hsn_code"] == "998399"                       # the user's own code is left alone
    assert all(c["data"].get("hsn_code") for c in charges.values())                # no charge is left without a code
    assert client.post("/api/masters/charge/complete-hsn", headers=h).json()["updated"] == []   # nothing left to do


def test_activation_of_a_catalogue_charge_still_needs_its_sac_and_rate(h):
    r = client.post("/api/masters/charge", json={"code": "ZZ-CAT", "name": "Zz Catalogue", "status": "INACTIVE", "data": {"catalogue": "yes", "tax_treatment": "taxable"}}, headers=h)
    assert r.status_code == 201, r.text
    blocked = client.patch(f"/api/masters/charge/{r.json()['id']}", json={"status": "ACTIVE"}, headers=h)
    assert blocked.status_code == 422 and "SAC" in blocked.json()["detail"]
