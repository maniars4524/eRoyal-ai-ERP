import uuid
from datetime import date, timedelta
from decimal import Decimal

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


def test_dashboard_requires_login_and_a_company(world):
    assert client.get("/api/dashboard/summary").status_code == 401
    assert client.get("/api/dashboard/summary", headers=hdr(world["dev"], portal="developer")).status_code == 403


def test_empty_company_has_zeroes_not_placeholders(world, admin, h):
    # a freshly created company: nothing but zeros
    s = client.get("/api/dashboard/summary", headers=h).json()
    assert s["jobs"]["active"] == 0 and s["jobs"]["mix"] == [] and s["jobs"]["recent"] == []
    assert s["money"]["revenue_mtd"] == "0" and s["money"]["ar_outstanding"] == "0"
    assert len(s["monthly"]) == 6 and all(m["jobs"] == 0 for m in s["monthly"])


def test_dashboard_reflects_real_jobs_and_billing(h):
    for mode, direction, pol, pod in [("Sea", "Export", "INMAA", "DEHAM"), ("Sea", "Export", "INMAA", "DEHAM"), ("Air", "Import", "SGSIN", "INMAA"), ("Road", "Domestic", "Chennai", "Hosur")]:
        assert client.post("/api/shipments", json={"mode": mode, "direction": direction, "pol": pol, "pod": pod}, headers=h).status_code == 201
    jobs = client.get("/api/shipments", headers=h).json()
    sea = next(j for j in jobs if j["mode"] == "Sea")
    # invoice 1,000 (sea job), purchase invoice 400 (same job), second invoice overdue
    inv = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Acme", "job_number": sea["job_number"], "lines": [{"charge_name": "x", "rate": "1000", "tax_type": "Non-Taxable"}]}, headers=h).json()
    client.post(f"/api/documents/{inv['id']}/post", headers=h)
    pi = client.post("/api/documents", json={"doc_type": "purchase_invoice", "party_name": "MSC", "job_number": sea["job_number"], "lines": [{"charge_name": "y", "rate": "400", "tax_type": "Non-Taxable"}]}, headers=h).json()
    client.post(f"/api/documents/{pi['id']}/post", headers=h)
    old = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Late Co", "doc_date": str(date.today() - timedelta(days=50)), "due_date": str(date.today() - timedelta(days=40)), "lines": [{"rate": "300", "tax_type": "Non-Taxable"}]}, headers=h).json()
    client.post(f"/api/documents/{old['id']}/post", headers=h)
    client.post("/api/payments", json={"direction": "RECEIPT", "amount": "250", "allocations": [{"document_id": inv["id"], "amount": "250"}]}, headers=h)

    s = client.get("/api/dashboard/summary", headers=h).json()
    assert s["jobs"]["active"] == 4 and s["jobs"]["total"] == 4
    assert {m["label"]: m["count"] for m in s["jobs"]["mix"]}["Sea Export"] == 2
    assert s["jobs"]["matrix"]["Sea Export"]["active"] == 2 and s["jobs"]["matrix"]["Air Import"]["active"] == 1 and s["jobs"]["matrix"]["Road / Transport"]["active"] == 1
    assert s["jobs"]["top_lanes"][0] == {"lane": "INMAA → DEHAM", "count": 2}
    assert s["money"]["ar_outstanding"] == "1050.00"  # 1000 - 250 paid + 300
    assert s["money"]["ageing"]["d31_60"] == "300.00" and s["money"]["ageing"]["not_due"] in ("750.00",)
    assert s["money"]["top_overdue"][0] == {"party": "Late Co", "amount": "300.00"}
    assert s["alerts"]["overdue_invoices"] == 1
    assert Decimal(s["money"]["revenue_mtd"]) >= Decimal("1000") and Decimal(s["money"]["gp_mtd"]) >= Decimal("600")
    assert s["monthly"][-1]["jobs"] == 4
    assert len(s["jobs"]["recent"]) == 4 and s["jobs"]["recent"][0]["route"]


def test_dashboard_is_tenant_isolated(world, h):
    demo = client.get("/api/dashboard/summary", headers=hdr(world["demo"], portal="demo")).json()
    assert demo["jobs"]["total"] == 0 and demo["money"]["ar_outstanding"] == "0"


def test_active_jobs_split_into_freight_cha_and_transport_without_double_counting(world, admin):
    from tests.test_cha_jobs import booking, client as cl, hdr as hd
    hh = hd(admin["username"], admin["license"])
    s0 = cl.get("/api/dashboard/summary", headers=hh).json()["jobs"]
    booking(hh, "Freight, CHA")
    done = cl.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Air", "exporter": "Cat A", "importer": "Cat B"}, headers=hh).json()
    s1 = cl.get("/api/dashboard/summary", headers=hh).json()["jobs"]
    c0, c1 = s0["categories"], s1["categories"]
    assert c1["cha"]["active"] == c0["cha"]["active"] + 2          # the booking's CHA job and the direct one
    assert s1["active"] == c1["freight"]["active"] + c1["cha"]["active"] + c1["transport"]["active"]          # each job in exactly one category
    assert sum(r["active"] for r in s1["matrix"].values()) == c1["freight"]["active"]          # the shipment-type table is freight only
    rows = {r["label"]: r for r in s1["cha_breakdown"]}
    assert rows["Total CHA jobs"]["active"] == c1["cha"]["active"] and rows["Created / documentation pending"]["active"] >= 1
    assert sum(r["active"] for l, r in rows.items() if l != "Total CHA jobs") == rows["Total CHA jobs"]["active"]          # the stage rows add up to the total: no job is counted twice
    assert s1["cha_split"]["Import"] + s1["cha_split"]["Export"] == c1["cha"]["active"]
    assert {"Export transportation", "Import delivery", "In transit"} <= {r["label"] for r in s1["transport_breakdown"]}
