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


def test_catalogue_import_is_inactive_idempotent_and_complete(h):
    r = client.post("/api/masters/charge/catalogue/import", headers=h)
    assert r.status_code == 201 and r.json()["charges_created"] == 47 and r.json()["headings_created"] >= 3
    again = client.post("/api/masters/charge/catalogue/import", headers=h).json()
    assert again["charges_created"] == 0 and again["charges_skipped"] == 47
    charges = {c["code"]: c for c in client.get("/api/masters/charge", headers=h).json()}
    assert len(charges) >= 47 and all(c["status"] == "INACTIVE" and c["data"]["tax_status"] == "REVIEW_REQUIRED" for k, c in charges.items() if c["data"].get("catalogue") == "yes")
    assert charges["SEA-FRT-DOM"]["data"]["gst_percent"] == "5" and charges["SEA-FRT-DOM"]["data"]["hsn_code"] == "9965"
    assert charges["AIR-FRT-EXP"]["data"]["gst_percent"] == ""  # conditional: no rate guessed
    assert charges["COURIER"]["data"]["hsn_code"] == "9968" and charges["THC"]["data"]["hsn_code"] == "9967"
    assert {x["code"] for x in client.get("/api/masters/hsn", headers=h).json()} >= {"9965", "9967", "9968"}
    # inactive charges are not offered in pick-lists
    assert all(c["code"] != "THC" for c in client.get("/api/masters/charge?status=ACTIVE", headers=h).json())


def test_activation_needs_verified_tax_configuration_and_is_audited(h):
    client.post("/api/masters/charge/catalogue/import", headers=h)
    charges = {c["code"]: c for c in client.get("/api/masters/charge", headers=h).json()}
    thc = charges["THC"]
    blocked = client.patch(f"/api/masters/charge/{thc['id']}", json={"status": "ACTIVE"}, headers=h)
    assert blocked.status_code == 422 and "notification" in blocked.json()["detail"]
    exp = charges["AIR-FRT-EXP"]
    d = dict(exp["data"], notification_ref="CBIC notification ref", effective_date="2026-04-01")
    assert client.patch(f"/api/masters/charge/{exp['id']}", json={"status": "ACTIVE", "data": d}, headers=h).status_code == 422  # conditional rate still unresolved
    d2 = dict(d, tax_treatment="zero_rated")
    ok = client.patch(f"/api/masters/charge/{exp['id']}", json={"status": "ACTIVE", "data": d2}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE"
    d3 = dict(thc["data"], notification_ref="Notification No. 11/2017-CT(R)", effective_date="2026-04-01")
    assert client.patch(f"/api/masters/charge/{thc['id']}", json={"status": "ACTIVE", "data": d3}, headers=h).status_code == 200
    db = SessionLocal()
    try:
        events = db.scalars(select(AuditLog).where(AuditLog.action == "CHARGE_TAX_ACTIVATED")).all()
        assert any(e.entity_id == str(thc["id"]) for e in events)
    finally:
        db.close()
    # an ordinary (non-catalogue) charge keeps working as before
    assert client.post("/api/masters/charge", json={"code": "PLAIN1", "name": "Plain", "data": {"hsn_code": "9965", "gst_percent": "18"}}, headers=h).status_code == 201


def test_standard_sac_import_is_idempotent_and_flags_conditional_rates(h):
    r = client.post("/api/masters/hsn/standard/import", headers=h)
    assert r.status_code == 201 and r.json()["created"] + r.json()["skipped"] == 21
    again = client.post("/api/masters/hsn/standard/import", headers=h).json()
    assert again["created"] == 0 and again["skipped"] == 21
    codes = {c["code"]: c for c in client.get("/api/masters/hsn", headers=h).json()}
    assert codes["996711"]["data"]["gst_rate"] == "18%" and codes["996521"]["data"]["gst_rate"] == "" and "verify" in codes["996521"]["data"]["tax_category"].lower()
    assert {"9965", "9967", "9968", "996812"} <= set(codes)


def test_full_sac_master_import_by_section(h):
    client.post("/api/masters/hsn/standard/import", headers=h)  # starter list first: the master file must enrich it, not duplicate it
    r = client.post("/api/masters/hsn/sac-master/import", headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["created"] + r.json()["enriched"] + r.json()["skipped"] == 571 and r.json()["enriched"] >= 15
    again = client.post("/api/masters/hsn/sac-master/import", headers=h).json()
    assert again["created"] == 0 and again["enriched"] == 0 and again["skipped"] == 571
    codes = {c["code"]: c for c in client.get("/api/masters/hsn?limit=1000", headers=h).json()}
    assert len(codes) >= 571
    sea = codes["996521"]["data"]
    assert sea["section"] == "6" and sea["heading"] == "9965" and sea["group"] == "99652" and sea["source"] == "sac_master" and "Coastal" in codes["996521"]["name"]
    assert codes["996711"]["data"]["gst_rate"] == "18%"  # proposed starter rate kept
    assert codes["997113"]["data"]["gst_rate"] == "" and codes["997113"]["data"]["section"] == "7"  # no rate invented for the rest
    assert {c["data"]["section"] for c in codes.values() if c["data"].get("source") == "sac_master"} == {"5", "6", "7", "8", "9"}
    assert len(codes["996521"]["name"]) <= 200 and len(codes["996521"]["data"]["full_description"]) >= len(codes["996521"]["name"])
