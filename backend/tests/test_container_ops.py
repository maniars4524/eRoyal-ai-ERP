"""Container operations: surveys -> damage -> repair work orders (M&R), leases with on / off-hire, EIR, depot stock counts and alerts."""
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from tests.test_bookings import client, hdr
from tests.test_containers import APPROVER, get, h, move, new_container, number, ok, setup  # noqa: F401  (fixtures and helpers)


@pytest.fixture(scope="module")
def ops(h, setup):
    tag = setup["tag"]
    for t, code, name, data in (("inv_survey_type", "GIN", "Gate-in survey", {}), ("inv_damage_type", "DNT", "Dent", {"default_severity": "Minor"}), ("inv_repair_category", "WLD", "Welding", {}),
                                ("inv_container_condition", "REP", "Repaired", {"available_for_use": "Yes"})):
        r = client.post(f"/api/masters/{t}", json={"code": f"{code}{tag}", "name": f"{name} {tag}", "data": data}, headers=h)
        assert r.status_code == 201, r.text
    return {**setup, "survey": f"Gate-in survey {tag}", "dent": f"Dent {tag}", "weld": f"Welding {tag}", "repaired": f"Repaired {tag}"}


def A(path, body=None, method="post"):
    """A call made by the separate approver."""
    return getattr(client, method)(path, json=body, headers=APPROVER["h"]) if body is not None or method != "post" else client.post(path, headers=APPROVER["h"])


def at_depot(h, ops, **kw):
    c = new_container(h, ops, **kw)
    assert ok(move(h, c, "IN", "RECEIPT", to_depot=ops["chn"]).json()).status_code == 200
    return c


# ------------------------------------------------------------------ survey -> damage
def test_survey_flow_creates_a_damage_record_and_enforces_the_workflow(h, ops):
    c = at_depot(h, ops)
    body = {"container_id": c["id"], "survey_type": ops["survey"], "survey_date": "2026-11-05", "surveyor": "R. Kumar", "severity": "Moderate", "damage_location": "Roof",
            "checklist": [{"item": "Roof", "result": "DAMAGED", "remarks": "dent 30cm"}, {"item": "Floor", "result": "OK"}], "repair_estimate": "4500"}
    assert client.post("/api/container-surveys", json={**body, "survey_type": "Nope"}, headers=h).status_code == 422
    s = client.post("/api/container-surveys", json=body, headers=h).json()
    assert s["status"] == "DRAFT" and s["number"] and s["depot"] == ops["chn"]
    assert A(f"/api/container-surveys/{s['id']}/approve").status_code == 409                       # must be submitted first
    assert client.post(f"/api/container-surveys/{s['id']}/submit", headers=h).status_code == 200
    assert client.patch(f"/api/container-surveys/{s['id']}", json=body, headers=h).status_code == 409   # locked once submitted
    r = A(f"/api/container-surveys/{s['id']}/approve")
    assert r.status_code == 200 and r.json()["damage_number"]
    dmg = client.get("/api/container-damages", params={"container_id": c["id"]}, headers=h).json()["items"][0]
    assert dmg["status"] == "OPEN" and dmg["severity"] == "Moderate" and D(dmg["estimated_cost"]) == 4500


def test_a_survey_needs_content_and_can_be_rejected(h, ops):
    c = at_depot(h, ops)
    empty = client.post("/api/container-surveys", json={"container_id": c["id"], "survey_type": ops["survey"], "survey_date": "2026-11-05"}, headers=h).json()
    assert client.post(f"/api/container-surveys/{empty['id']}/submit", headers=h).status_code == 422
    full = client.post("/api/container-surveys", json={"container_id": c["id"], "survey_type": ops["survey"], "survey_date": "2026-11-05", "checklist": [{"item": "Roof", "result": "OK"}]}, headers=h).json()
    client.post(f"/api/container-surveys/{full['id']}/submit", headers=h)
    assert A(f"/api/container-surveys/{full['id']}/reject", {"reason": "photos missing"}).status_code == 200
    assert client.get(f"/api/container-surveys/{full['id']}", headers=h).json()["status"] == "REJECTED"
    assert client.get("/api/container-damages", params={"container_id": c["id"]}, headers=h).json()["total"] == 0     # a clean survey opens no damage


# ------------------------------------------------------------------ repair
def repair_body(c, ops, **kw):
    return {"container_id": c["id"], "category": ops["weld"], "description": "weld roof dent", "vendor": "Fix-It Works", "labour": "1000", "material": "2000", "welding": "500", **kw}


def test_repair_workflow_blocks_release_until_quality_approval(h, ops):
    c = at_depot(h, ops)
    r = client.post("/api/container-repairs", json=repair_body(c, ops), headers=h)
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["status"] == "ESTIMATED" and D(w["estimated_total"]) == 3500 and w["number"]
    assert client.post("/api/container-repairs", json={"container_id": c["id"]}, headers=h).status_code == 422                 # no cost entered
    assert client.post(f"/api/container-repairs/{w['id']}/start", headers=h).status_code == 409                                # not approved yet
    assert A(f"/api/container-repairs/{w['id']}/approve").status_code == 200
    assert get(h, c)["availability"] == "UNDER_REPAIR"                                                                         # a repair-pending container is not available
    assert move(h, c, "OUT", "RELEASE", movement_date="2026-11-12").status_code == 409
    assert client.post(f"/api/container-repairs/{w['id']}/start", headers=h).status_code == 200
    assert client.post(f"/api/container-repairs/{w['id']}/complete", json={"actual_cost": "3800", "completion_date": str(date.today() + timedelta(days=2))}, headers=h).status_code == 200
    done = client.get(f"/api/container-repairs/{w['id']}", headers=h).json()
    assert done["downtime_days"] == 3 and done["over_approved_amount"] is True                                                # 3,800 against 3,500 approved
    assert get(h, c)["availability"] == "UNDER_REPAIR"                                                                         # still not usable before quality approval
    assert A(f"/api/container-repairs/{w['id']}/qc", {"condition_after": ops["repaired"]}).status_code == 200
    now = get(h, c)
    assert now["condition"] == ops["repaired"] and now["availability"] == "AVAILABLE"
    assert any(x["field"] == "condition" and "quality approved" in x["reason"] for x in now["history"])
    assert move(h, c, "OUT", "RELEASE", movement_date="2026-11-13").status_code == 201


def test_supplementary_estimate_raises_the_approved_limit(h, ops):
    c = at_depot(h, ops)
    w = client.post("/api/container-repairs", json=repair_body(c, ops), headers=h).json()
    A(f"/api/container-repairs/{w['id']}/approve")
    assert client.post(f"/api/container-repairs/{w['id']}/supplement", json={"amount": "800", "reason": "extra panel found"}, headers=h).status_code == 200
    assert D(client.get(f"/api/container-repairs/{w['id']}", headers=h).json()["approved_with_supplements"]) == 3500
    assert A(f"/api/container-repairs/{w['id']}/supplement/0/approve", method="post", body={}).status_code == 200
    assert D(client.get(f"/api/container-repairs/{w['id']}", headers=h).json()["approved_with_supplements"]) == 4300


def test_cancel_reject_and_reopen_keep_the_container_safe(h, ops):
    c = at_depot(h, ops)
    w = client.post("/api/container-repairs", json=repair_body(c, ops), headers=h).json()
    A(f"/api/container-repairs/{w['id']}/approve")
    assert client.post(f"/api/container-repairs/{w['id']}/cancel", json={"reason": "not needed"}, headers=APPROVER["h"]).status_code == 200
    assert get(h, c)["availability"] == "NOT_AVAILABLE"                                                                        # needs a condition check before use
    w2 = client.post("/api/container-repairs", json=repair_body(c, ops), headers=h).json()
    assert A(f"/api/container-repairs/{w2['id']}/reject", {"reason": "too expensive"}).status_code == 200
    assert A(f"/api/container-repairs/{w2['id']}/reopen", {"reason": "re-quoted"}).status_code == 200
    assert client.get(f"/api/container-repairs/{w2['id']}", headers=h).json()["status"] == "ESTIMATED"
    disposed = at_depot(h, ops)
    assert ok(move(h, disposed, "OUT", "DISPOSAL", movement_date="2026-11-14").json()).status_code == 200
    assert client.post("/api/container-repairs", json=repair_body(disposed, ops), headers=h).status_code == 409


def test_repair_summary_uses_real_orders(h, ops):
    s = client.get("/api/container-repairs/summary", headers=h).json()
    assert s["orders"] >= 1 and "ESTIMATED" in s["by_status"] and "Fix-It Works" in s["cost_by_vendor"]


# ------------------------------------------------------------------ lease
def lease_body(**kw):
    return {"agreement_number": f"LSE-{uuid.uuid4().hex[:6].upper()}", "leasing_company": "Global Lessor Ltd", "start_date": "2026-01-01", "end_date": "2026-12-31", "daily_rental": "1.50", "currency": "USD",
            "free_days": 5, "min_lease_days": 30, **kw}


def test_lease_on_hire_off_hire_cost_and_company_asset_rule(h, ops):
    lease = client.post("/api/container-leases", json=lease_body(), headers=h).json()
    assert client.post("/api/container-leases", json={**lease_body(), "agreement_number": lease["agreement_number"]}, headers=h).status_code == 409
    owned = new_container(h, ops, ownership="own")
    r = client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": owned["id"], "on_hire_date": "2026-02-01"}, headers=h)
    assert r.status_code == 409 and "company fixed asset" in r.json()["detail"]                    # a leased container is never company-owned
    c = new_container(h, ops, ownership="lease")
    assert client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": c["id"], "on_hire_date": "2025-12-01"}, headers=h).status_code == 422
    r = client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": c["id"], "on_hire_date": "2026-02-01"}, headers=h)
    assert r.status_code == 201 and r.json()["containers_on_hire"] == 1
    assert get(h, c)["lease_status"] == "ON_HIRE"
    assert client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": c["id"], "on_hire_date": "2026-02-02"}, headers=h).status_code == 409
    assert client.post(f"/api/container-leases/{lease['id']}/close", headers=h).status_code == 409           # still on hire
    item = r.json()["items"][0]
    off = client.post(f"/api/container-leases/items/{item['id']}/off-hire", json={"off_hire_date": "2026-02-10"}, headers=h)
    assert off.status_code == 200
    it = off.json()["items"][0]
    assert it["days"] == 10 and it["free_days"] == 5 and it["billable_days"] == 30 and it["amount"] == "45.00"   # minimum lease period of 30 days at 1.50
    assert get(h, c)["lease_status"] == "OFF_HIRE"
    assert client.post(f"/api/container-leases/{lease['id']}/close", headers=h).json()["status"] == "CLOSED"


def test_lease_extension_and_overdue_redelivery_alert(h, ops):
    past = (date.today() - timedelta(days=3)).isoformat()
    lease = client.post("/api/container-leases", json=lease_body(start_date="2026-01-01", end_date=past), headers=h).json()
    c = new_container(h, ops, ownership="lease")
    client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": c["id"], "on_hire_date": "2026-01-05"}, headers=h)
    al = client.get("/api/container-alerts", headers=h).json()
    mine = next(x for x in al["leases"] if x["agreement_number"] == lease["agreement_number"])
    assert mine["overdue_redelivery"] is True and al["total"] >= 1
    assert client.post(f"/api/container-leases/{lease['id']}/extend", json={"new_end_date": past, "reason": "no change"}, headers=APPROVER["h"]).status_code == 422
    new_end = (date.today() + timedelta(days=90)).isoformat()
    assert client.post(f"/api/container-leases/{lease['id']}/extend", json={"new_end_date": new_end, "reason": "customer needs more time"}, headers=APPROVER["h"]).status_code == 200
    assert not any(x["agreement_number"] == lease["agreement_number"] for x in client.get("/api/container-alerts", headers=h).json()["leases"])


# ------------------------------------------------------------------ EIR, stock count, CSC alert
def test_eir_is_issued_once_for_an_approved_movement(h, ops):
    c = new_container(h, ops)
    draft = move(h, c, "IN", "RECEIPT", to_depot=ops["chn"]).json()
    assert client.post("/api/container-eirs", json={"movement_id": draft["id"]}, headers=h).status_code == 409           # not approved
    ok(draft)
    r = client.post("/api/container-eirs", json={"movement_id": draft["id"], "driver_signed_by": "Driver A", "party_signed_by": "Gate clerk"}, headers=h)
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["number"] and e["container_number"] == c["container_number"] and e["to_depot"] == ops["chn"]
    assert client.post("/api/container-eirs", json={"movement_id": draft["id"]}, headers=h).status_code == 409
    assert client.get(f"/api/container-eirs/by-movement/{draft['id']}", headers=h).json()["number"] == e["number"]
    assert client.get(f"/api/container-movements/{draft['id']}", headers=h).json()["eir_number"] == e["number"]


def test_stock_count_finds_missing_and_unexpected_containers(h, ops):
    a, b = at_depot(h, ops), at_depot(h, ops)
    other = new_container(h, ops)                                                                  # registered but not at a depot
    ghost = number("777001")
    r = client.post("/api/container-counts", json={"depot": ops["chn"], "count_date": "2026-11-05", "numbers": [a["container_number"], other["container_number"], ghost]}, headers=h)
    assert r.status_code == 201, r.text
    k = r.json()
    assert b["container_number"] in {x["number"] for x in k["missing"]} and a["container_number"] not in {x["number"] for x in k["missing"]}
    notes = {x["number"]: x["note"] for x in k["unexpected"]}
    assert "not at depot" in notes[other["container_number"]] and notes[ghost] == "Not in the register"
    assert A(f"/api/container-counts/{k['id']}/review", {"reason": "checked with yard"}).status_code == 200
    assert A(f"/api/container-counts/{k['id']}/review", {"reason": "again"}).status_code == 409
    assert client.post("/api/container-counts", json={"depot": "Nowhere", "count_date": "2026-11-05", "numbers": []}, headers=h).status_code == 422


def test_csc_expiry_shows_in_alerts(h, ops):
    c = new_container(h, ops)
    client.patch(f"/api/containers/{c['id']}", json={"csc_valid_until": (date.today() - timedelta(days=1)).isoformat()}, headers=h)
    csc = client.get("/api/container-alerts", headers=h).json()["csc"]
    assert any(x["container_number"] == c["container_number"] and x["expired"] for x in csc)
