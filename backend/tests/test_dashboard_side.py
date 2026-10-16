from datetime import date, timedelta

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


@pytest.fixture(scope="module")
def h2(world, admin2):
    return hdr(admin2["username"], admin2["license"])


def _quote(h):
    line = {"side": "SELL", "charge_name": "Ocean Freight", "carrier": "", "basis": "Per Container", "ccy": "USD", "exch": "83.5", "rate": "850", "qty": "2", "tax_pct": "0"}
    return client.post("/api/quotations", json={"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "BEANR", "lines": [line]}, headers=h).json()


def test_pending_approvals_task_and_news_are_per_user(h, h2):
    q = _quote(h)
    assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).status_code == 200
    # the approver sees it as waiting for them; the list names the type, the party and who submitted it
    side2 = client.get("/api/dashboard/side", headers=h2).json()
    item = next(a for a in side2["approvals"] if a["id"] == q["id"])
    assert item["type"] == "Quotation" and item["party"] == "Acme Exports" and item["own"] is False and item["by"]
    assert side2["counts"]["approvals"] >= 1
    # approving removes it and tells the person who prepared it
    assert client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2).status_code == 200
    assert all(a["id"] != q["id"] or a["type"] != "Quotation" for a in client.get("/api/dashboard/side", headers=h2).json()["approvals"])
    news = client.get("/api/dashboard/side", headers=h).json()["notifications"]
    assert any("approved by" in n["text"] and n["open"] == "quotation" and n["id"] == q["id"] for n in news)
    # a follow-up due today is a task for the user who created it, and for nobody else
    due = date.today().isoformat()
    assert client.put("/api/crm/followup", json={"ref": "FU-T-1", "company": "Acme", "status": "open", "data": {"type": "Call", "relatedLabel": "Acme Exports", "due": due, "done": False, "notes": "Confirm rates"}}, headers=h).status_code in (200, 201)
    assert client.put("/api/crm/followup", json={"ref": "FU-T-2", "company": "Acme", "status": "open", "data": {"type": "Call", "relatedLabel": "Later", "due": (date.today() + timedelta(days=5)).isoformat(), "done": False}}, headers=h).status_code in (200, 201)
    mine = client.get("/api/dashboard/side", headers=h).json()["tasks"]
    assert [t["ref"] for t in mine if t["ref"].startswith("FU-T")] == ["FU-T-1"] and mine[0]["go"] == "crm-engagement"  # only what is due; the future one waits
    assert not any(t["ref"].startswith("FU-T") for t in client.get("/api/dashboard/side", headers=h2).json()["tasks"])
    # activity shows what happened, newest first
    act = client.get("/api/dashboard/side?limit=30", headers=h).json()["activity"]
    assert any("Quotation approved" in a["text"] for a in act) and all(a["by"] for a in act)


def test_nothing_waits_for_someone_without_approval_rights(world, admin, h):
    from app.models import User  # a plain viewer never receives approval work
    import uuid
    name = "dv" + uuid.uuid4().hex[:6]
    assert client.post("/api/users", json={"username": name, "email": name + "@t.test", "password": PASSWORD, "first_name": "V", "role": "VIEWER"}, headers=h).status_code in (200, 201)
    hv = hdr(name, admin["license"])
    q = _quote(h)
    client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h)
    assert client.get("/api/dashboard/side", headers=hv).json()["approvals"] == []
