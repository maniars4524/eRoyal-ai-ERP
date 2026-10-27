"""HBL Clause Master, the clause selection on a job's BL tab, the snapshot that master edits cannot change, and the checks before the BL is approved / released."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def hdr(username, license=None):
    body = {"identifier": username, "password": PASSWORD, "portal": "erp"}
    if license:
        body["license_number"] = license
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def job(h):
    r = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA(CHENNAI)", "pod": "USLAX(LOS ANGELES)"}, headers=h)
    assert r.status_code == 201, r.text
    j = r.json()["job_number"]
    assert client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h).status_code == 200
    return j


def mk(h, title, text=None, **kw):
    r = client.post("/api/hbl-clauses", json={"title": title, "text": text or title + " " + uuid.uuid4().hex[:6], **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_master_create_search_duplicate_deactivate_and_sample(h):
    a = mk(h, "Test freight clause", "FREIGHT PREPAID TEST " + uuid.uuid4().hex[:5].upper(), category="Freight", freight_term="Prepaid")
    assert a["code"].startswith("HBLC-") and a["status"] == "ACTIVE" and a["created_by"]
    assert client.post("/api/hbl-clauses", json={"title": "dup", "text": a["text"]}, headers=h).status_code == 409           # the same wording twice
    found = client.get("/api/hbl-clauses", params={"q": a["code"]}, headers=h).json()
    assert found["total"] == 1 and found["items"][0]["id"] == a["id"] and found["stats"]["total"] >= 1
    assert client.patch(f"/api/hbl-clauses/{a['id']}", json={"status": "INACTIVE"}, headers=h).json()["status"] == "INACTIVE"
    assert client.get("/api/hbl-clauses", params={"status": "ACTIVE", "q": a["code"]}, headers=h).json()["total"] == 0
    assert client.post("/api/hbl-clauses", json={"title": "x", "text": "y", "category": "Nope"}, headers=h).status_code == 422
    first = client.post("/api/hbl-clauses/load-sample", headers=h).json()
    assert first["created"] >= 55
    again = client.post("/api/hbl-clauses/load-sample", headers=h).json()
    assert again["created"] == 0 and again["skipped"] >= 55           # additive, never duplicated
    assert client.get("/api/hbl-clauses", params={"q": "FOR ALL DOOR AND HOUSE DELIVERIES"}, headers=h).json()["total"] >= 1


def test_selection_is_snapshotted_ordered_deduplicated_and_survives_master_edits(h):
    c1 = mk(h, "Snap one", "SNAPSHOT ONE " + uuid.uuid4().hex[:6])
    c2 = mk(h, "Snap two", "SNAPSHOT TWO " + uuid.uuid4().hex[:6])
    j = job(h)
    info = client.get(f"/api/jobs/{j}/hbl-clauses", headers=h).json()
    assert info["stage"] == "Draft" and not info["locked"] and not info["saved"] and info["hbl_number"]
    saved = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": c2["id"]}, {"clause_id": c1["id"]}, {"clause_id": c2["id"]}]}, headers=h)
    assert saved.status_code == 200, saved.text
    sel = saved.json()["selected"]
    assert [s["code"] for s in sel] == [c2["code"], c1["code"]] and saved.json()["saved"] and saved.json()["version"] == 1       # order kept, duplicate dropped
    client.patch(f"/api/hbl-clauses/{c1['id']}", json={"text": "CHANGED WORDING " + uuid.uuid4().hex[:6]}, headers=h)
    client.patch(f"/api/hbl-clauses/{c2['id']}", json={"status": "INACTIVE"}, headers=h)
    again = client.get(f"/api/jobs/{j}/hbl-clauses", headers=h).json()["selected"]
    assert [s["text"] for s in again] == [c2["text"], c1["text"]]                           # the saved BL keeps the wording it was saved with
    rd = client.get(f"/api/jobs/{j}/report-data", headers=h).json()["details"]["bl"]
    assert [s["text"] for s in rd["clauses"]] == [c2["text"], c1["text"]]                   # and the print reads the snapshot
    # saving the BL form does not wipe the clause snapshot
    client.put(f"/api/jobs/{j}/details/bl", json={"data": {"bookingType": "House", "terms": "Prepaid"}}, headers=h)
    assert len(client.get(f"/api/jobs/{j}/hbl-clauses", headers=h).json()["selected"]) == 2
    # a one-time clause for this HBL only
    one = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": c1["id"]}, {"title": "Once", "text": "ONLY ON THIS BL " + uuid.uuid4().hex[:5]}]}, headers=h)
    assert one.status_code == 200 and one.json()["selected"][1]["source"] == "custom" and one.json()["version"] == 2


def test_mandatory_conflicts_and_stage_checks(h):
    tag = uuid.uuid4().hex[:5].upper()
    must = mk(h, "Must " + tag, "MANDATORY FOR TEST " + tag, is_mandatory=True, is_default=True, shipment_type="FCL")
    lcl = mk(h, "LCL only " + tag, "LCL ONLY TEST " + tag, shipment_type="LCL", is_mandatory=True)       # does not fit an FCL job: never required
    g1 = mk(h, "Group a " + tag, "GROUP A " + tag, exclusive_group="grp-" + tag.lower())
    g2 = mk(h, "Group b " + tag, "GROUP B " + tag, exclusive_group="grp-" + tag.lower())
    j = job(h)
    info = client.get(f"/api/jobs/{j}/hbl-clauses", headers=h).json()
    assert must["id"] in [c["id"] for c in info["suggested"]] and lcl["id"] not in [c["id"] for c in info["available"]]
    # confirming without a saved selection is refused with a clear message
    refused = client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Confirmed"}, headers=h)
    assert refused.status_code == 422 and "Save the HBL clause selection" in refused.text
    # conflicting clauses are refused, nothing is changed silently
    clash = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}, {"clause_id": g1["id"]}, {"clause_id": g2["id"]}]}, headers=h)
    assert clash.status_code == 422 and "Conflicting" in clash.text
    # the mandatory clause cannot be dropped without the override right and a reason (the test admin holds the right, so the reason is what is checked)
    assert client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": g1["id"]}]}, headers=h).status_code == 422
    ok = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}, {"clause_id": g1["id"]}]}, headers=h)
    assert ok.status_code == 200 and not ok.json()["problems"]
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Confirmed"}, headers=h).status_code == 200
    # an approved BL: clauses are locked until it is returned to Draft (re-approval)
    locked = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}]}, headers=h)
    assert locked.status_code == 409 and "return it to Draft" in locked.text
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Draft"}, headers=h).status_code == 200
    assert client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}]}, headers=h).status_code == 200
    # a released BL is locked for good
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Confirmed"}, headers=h).status_code == 200
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Released", "release_type": "Express Release"}, headers=h).status_code == 200
    assert client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}]}, headers=h).status_code == 409
    # tidy: the mandatory clauses of this test must not affect other tests' jobs
    for c in (must, lcl):
        client.patch(f"/api/hbl-clauses/{c['id']}", json={"status": "INACTIVE"}, headers=h)


def test_company_without_clauses_works_as_before(world, admin2):
    other = hdr(admin2["username"], admin2["license"])
    j = job(other)
    # the same company as `h`, but no mandatory clause is active any more: confirm / release behave exactly as before
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Confirmed"}, headers=other).status_code == 200


def test_ordinary_user_cannot_drop_a_mandatory_clause_or_add_a_one_time_clause(world, admin, h):
    tag = uuid.uuid4().hex[:5].upper()
    must = mk(h, "Must ops " + tag, "MANDATORY OPS " + tag, is_mandatory=True)
    client.post("/api/users", json={"username": f"op{tag.lower()}", "email": f"op{tag.lower()}@c.test", "password": PASSWORD, "first_name": "O", "role": "OPERATIONS"}, headers=h)
    ops = hdr(f"op{tag.lower()}", admin["license"])
    j = job(h)
    dropped = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": []}, headers=ops)
    assert dropped.status_code == 422 and "cannot be removed" in dropped.text
    custom = client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}, {"text": "MY OWN CLAUSE " + tag}]}, headers=ops)
    assert custom.status_code == 403
    assert client.put(f"/api/jobs/{j}/hbl-clauses", json={"items": [{"clause_id": must["id"]}]}, headers=ops).status_code == 200
    assert client.post("/api/hbl-clauses", json={"title": "x" * 3, "text": "NOT ALLOWED " + tag}, headers=ops).status_code == 403          # master maintenance needs master rights
    client.patch(f"/api/hbl-clauses/{must['id']}", json={"status": "INACTIVE"}, headers=h)
