import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"
NOW = lambda: datetime.now(timezone.utc).isoformat()


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
def viewer(h, admin):
    name = "jv" + uuid.uuid4().hex[:6]
    assert client.post("/api/users", json={"username": name, "email": name + "@t.test", "password": PASSWORD, "first_name": "V", "role": "VIEWER"}, headers=h).status_code in (200, 201)
    return hdr(name, admin["license"])


LINES = [{"charge_name": "Ocean Freight", "qty": "1", "hsn": "9965", "buy": {"ccy": "INR", "rate": "6000", "tax_type": "Non-Taxable", "party": "MSC"}, "sell": {"ccy": "INR", "rate": "10000", "tax_type": "Taxable", "tax_pct": "18", "party": "Acme"}}]


def new_job(h, mode="Sea", direction="Export", container_mode="FCL", charges=True):
    j = client.post("/api/shipments", json={"mode": mode, "direction": direction, "container_mode": container_mode, "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    if charges:
        assert client.put(f"/api/shipments/by-number/{j['job_number']}/charges", json={"lines": LINES}, headers=h).status_code == 200
    return j["job_number"]


def life(h, jn):
    return client.get(f"/api/jobs/{jn}/lifecycle", headers=h).json()


def ms(h, jn, code, when=None, **extra):
    return client.put(f"/api/jobs/{jn}/milestones/{code}", json={"actual_at": when or NOW(), **extra}, headers=h)


def events(h, jn, **params):
    return client.get(f"/api/jobs/{jn}/activity", params={"limit": 200, **params}, headers=h).json()


def invoice_job(h, jn):
    d = client.post("/api/documents/from-job", json={"job_number": jn, "doc_type": "invoice"}, headers=h)
    assert d.status_code == 201, d.text
    assert client.post(f"/api/documents/{d.json()['id']}/post", headers=h).status_code == 200


def ready_job(h):
    jn = new_job(h)
    shipment = next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == jn)
    assert client.patch(f"/api/shipments/{shipment['id']}", json={"bl_awb": "MAEU123456"}, headers=h).status_code == 200
    for code in ("documentation_completed", "cargo_received", "customs_cleared", "departed"):
        assert ms(h, jn, code).status_code == 200
    invoice_job(h, jn)
    return jn, shipment["id"]


ACK = {"ack": {"no_buy_cost": False, "accrual": True, "cost_sheet_reviewed": True, "discrepancy": False}, "remarks": "Final cost review completed"}


def test_creation_initial_stage_event_and_separate_closure_status(h):
    jn = new_job(h, charges=False)
    lc = life(h, jn)
    assert lc["job_stage"] == "Job Created" and lc["closure_status"] == "OPEN" and lc["closure_cycle"] == 0 and lc["event_count"] >= 1
    ev = events(h, jn)
    assert ev["total"] >= 1 and ev["items"][-1]["event_type"] == "JOB_CREATED" and ev["items"][-1]["actor"] and ev["items"][-1]["category"] == "JOB"
    assert client.get(f"/api/jobs/{jn}/stage-history", headers=h).json()[-1]["new_stage"] == "Job Created"


def test_milestones_drive_the_stage_never_planned_dates_or_the_future(h):
    jn = new_job(h)
    shipment = next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == jn)
    client.patch(f"/api/shipments/{shipment['id']}", json={"etd": "2020-01-01", "eta": "2020-02-01"}, headers=h)  # long-past PLANNED dates
    assert life(h, jn)["job_stage"] == "Job Created"
    assert ms(h, jn, "departed", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()).status_code == 422  # not actual yet
    assert ms(h, jn, "in_transit").status_code == 200  # out-of-sequence is valid
    assert life(h, jn)["job_stage"] == "In Transit"
    assert ms(h, jn, "documentation_started").status_code == 200
    assert life(h, jn)["job_stage"] == "In Transit"  # a lower-ranked event never moves the stage back
    dup = ms(h, jn, "documentation_started")
    assert dup.status_code == 409  # already confirmed: a correction needs a reason
    assert ms(h, jn, "documentation_started", correction_reason="Wrong day entered").status_code == 200
    hist = client.get(f"/api/jobs/{jn}/stage-history", headers=h).json()
    assert [x["new_stage"] for x in hist][:1] == ["In Transit"] and hist[0]["source"] == "AUTO" and "MILESTONE:in_transit" in hist[0]["trigger"]
    assert any(e["event_type"] == "MILESTONE_CORRECTED" and e["previous_value"] for e in events(h, jn)["items"])
    air = new_job(h, mode="Air", direction="Export", container_mode=None)
    assert ms(h, air, "empty_returned").status_code == 422  # not part of an air job


def test_manual_stage_needs_reason_warns_on_conflict_and_does_not_fake_milestones(h, viewer):
    jn = new_job(h)
    assert client.post(f"/api/jobs/{jn}/stage", json={"stage": "Documentation Completed", "reason": "x"}, headers=h).status_code == 422
    r = client.post(f"/api/jobs/{jn}/stage", json={"stage": "Customs Cleared", "reason": "Customer confirmed docs"}, headers=h)
    assert r.status_code == 409 and "STAGE_CONFLICT" in r.json()["detail"]
    ok = client.post(f"/api/jobs/{jn}/stage", json={"stage": "Customs Cleared", "reason": "Customer confirmed docs", "confirm_conflict": True}, headers=h)
    assert ok.status_code == 200 and ok.json()["job_stage"] == "Customs Cleared" and ok.json()["stage_source"] == "MANUAL"
    mm = {m["code"]: m for m in life(h, jn)["milestones"]}
    assert mm["customs_cleared"]["done"] is False and mm["documentation_completed"]["done"] is False  # nothing was completed behind the scenes
    assert client.post(f"/api/jobs/{jn}/stage", json={"stage": "Delivered", "reason": "try"}, headers=viewer).status_code == 403
    assert any(e["event_type"] == "PERMISSION_DENIED" and e["result"] == "REJECTED" for e in events(h, jn)["items"])
    ms(h, jn, "cargo_received")
    lc = life(h, jn)
    assert lc["job_stage"] == "Customs Cleared" and lc["stage_source"] == "MANUAL"  # lower evidence does not discard the manual stage
    ms(h, jn, "departed")
    lc = life(h, jn)
    assert lc["job_stage"] == "Departed / Sailed" and lc["stage_source"] == "AUTO"
    hist = client.get(f"/api/jobs/{jn}/stage-history", headers=h).json()
    assert "supersedes manual stage" in hist[0]["trigger"] and any(x["source"] == "MANUAL" and x["reason"] == "Customer confirmed docs" for x in hist)  # manual record kept


def test_ready_for_closure_is_a_stage_and_the_job_stays_open(h):
    jn, _ = ready_job(h)
    lc = life(h, jn)
    assert lc["job_stage"] == "Ready for Closure" and lc["closure_status"] == "OPEN" and lc["actions"]["can_close"] is True


def test_closure_checklist_blocks_until_requirements_are_met(h):
    jn = new_job(h)
    r = client.post(f"/api/jobs/{jn}/close", json={}, headers=h)
    assert r.status_code == 422 and len(r.json()["detail"]["pending"]) >= 4
    assert life(h, jn)["closure_status"] == "OPEN"
    failed = [e for e in events(h, jn)["items"] if e["event_type"] == "JOB_CLOSURE_FAILED"]
    assert failed and failed[0]["result"] == "FAILED"
    assert not any(e["event_type"] == "JOB_CLOSED" for e in events(h, jn)["items"])  # a failed attempt is never recorded as a closure
    v = client.post(f"/api/jobs/{jn}/closure/validate", json={}, headers=h).json()
    assert v["ready"] is False and {i["group"][0] for i in v["items"]} >= {"A", "B", "C", "D", "E", "F"}
    # sea import LCL: no empty-container requirement, air: no sailing requirement
    air = new_job(h, mode="Air", direction="Export", container_mode=None)
    keys = {i["key"] for i in client.post(f"/api/jobs/{air}/closure/validate", json={}, headers=h).json()["items"]}
    assert "ms_departed" in keys and "ms_empty_returned" not in keys


def test_manual_close_reopen_and_repeated_cycles_keep_every_record(h):
    jn, sid = ready_job(h)
    assert client.post(f"/api/jobs/{jn}/close", json={}, headers=h).status_code == 422  # acknowledgements still missing (cost sheet review)
    c1 = client.post(f"/api/jobs/{jn}/close", json=ACK, headers=h)
    assert c1.status_code == 200 and c1.json()["closure_status"] == "CLOSED" and c1.json()["closure_cycle"] == 1 and c1.json()["job_stage"] == "Ready for Closure"
    assert client.post(f"/api/jobs/{jn}/close", json=ACK, headers=h).status_code == 409  # no duplicate closure
    # closed job: the controlled fields are read-only on the API
    assert client.patch(f"/api/shipments/{sid}", json={"consignee": "Changed"}, headers=h).status_code == 409
    assert client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": LINES}, headers=h).status_code == 409
    assert client.post("/api/documents/from-job", json={"job_number": jn, "doc_type": "invoice"}, headers=h).status_code == 409
    assert ms(h, jn, "arrived").status_code == 409
    assert client.delete(f"/api/shipments/{sid}", headers=h).status_code == 409
    # reopen
    assert client.post(f"/api/jobs/{jn}/reopen", json={"reason": "x"}, headers=h).status_code == 422
    r = client.post(f"/api/jobs/{jn}/reopen", json={"reason": "Additional Vendor Invoice Received", "remarks": "MSC sent a revised bill"}, headers=h)
    assert r.status_code == 200 and r.json()["closure_status"] == "REOPENED" and r.json()["reopen_count"] == 1 and r.json()["job_number"] == jn
    assert client.post(f"/api/jobs/{jn}/reopen", json={"reason": "again"}, headers=h).status_code == 409  # only a closed job can be reopened
    assert client.patch(f"/api/shipments/{sid}", json={"consignee": "Corrected"}, headers=h).status_code == 200  # editing restored
    c2 = client.post(f"/api/jobs/{jn}/close", json=ACK, headers=h)
    assert c2.status_code == 200 and c2.json()["closure_cycle"] == 2
    cl = client.get(f"/api/jobs/{jn}/closures", headers=h).json()
    assert [(c["action"], c["cycle_no"]) for c in cl] == [("CLOSE", 2), ("REOPEN", 1), ("CLOSE", 1)]
    assert cl[0]["previous_closure_id"] == cl[2]["id"] and cl[1]["previous_closure_id"] == cl[2]["id"] and cl[2]["final_sell"] == "10000.00" and cl[2]["final_buy"] == "6000.00" and cl[2]["final_margin"] == "4000.00"
    types = [e["event_type"] for e in events(h, jn, order="asc")["items"]]
    assert types.count("JOB_CLOSED") == 2 and types.count("JOB_REOPENED") == 1 and types[0] == "JOB_CREATED"
    reopen = next(e for e in events(h, jn)["items"] if e["event_type"] == "JOB_REOPENED")
    assert reopen["reason"] == "Additional Vendor Invoice Received" and reopen["actor"] and reopen["previous_value"] == "CLOSED" and reopen["new_value"] == "REOPENED"
    # finance may still settle a closed job: payments are not blocked by closure (Finance permissions apply)
    inv = next(d for d in client.get("/api/documents", headers=h).json() if d["job_number"] == jn and d["status"] == "POSTED")
    assert client.post("/api/payments", json={"direction": "RECEIPT", "party_name": inv["party_name"] or "Acme", "amount": "100", "allocations": [{"document_id": inv["id"], "amount": "100"}]}, headers={**h, "Idempotency-Key": uuid.uuid4().hex}).status_code in (200, 201)


def test_permissions_and_idempotency(h, viewer):
    jn, _ = ready_job(h)
    assert client.post(f"/api/jobs/{jn}/close", json=ACK, headers=viewer).status_code == 403
    key = uuid.uuid4().hex
    first = client.post(f"/api/jobs/{jn}/close", json=ACK, headers={**h, "Idempotency-Key": key})
    again = client.post(f"/api/jobs/{jn}/close", json=ACK, headers={**h, "Idempotency-Key": key})
    assert first.status_code == 200 and again.status_code == 200 and again.json().get("already_processed") is True
    assert client.get(f"/api/jobs/{jn}/closures", headers=h).json().__len__() == 1
    assert client.post(f"/api/jobs/{jn}/reopen", json={"reason": "Cost Adjustment"}, headers=viewer).status_code == 403
    assert client.get(f"/api/jobs/{jn}/activity", headers=viewer).status_code == 200  # viewers can read history
    assert client.get(f"/api/jobs/{jn}/activity/export", headers=viewer).status_code == 403  # but not export it


def test_concurrent_close_creates_one_closure(h):
    jn, _ = ready_job(h)
    codes = []

    def go():
        codes.append(client.post(f"/api/jobs/{jn}/close", json=ACK, headers=h).status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(200) == 1 and all(c in (200, 409) for c in codes)
    assert len(client.get(f"/api/jobs/{jn}/closures", headers=h).json()) == 1


def test_exceptional_closure_only_when_company_policy_allows(h):
    jn = new_job(h, charges=False)
    body = {"exceptional": True, "justification": "Customer insolvency - write-off approved by the board"}
    assert client.post(f"/api/jobs/{jn}/close", json=body, headers=h).status_code == 409  # policy off
    assert client.post("/api/masters/setting", json={"code": "JOB_EXCEPTIONAL_CLOSURE", "name": "Allow exceptional closure", "data": {"enabled": "yes"}}, headers=h).status_code in (201, 409)
    assert client.post(f"/api/jobs/{jn}/close", json={"exceptional": True, "justification": "short"}, headers=h).status_code == 422
    ok = client.post(f"/api/jobs/{jn}/close", json=body, headers=h)
    assert ok.status_code == 200 and ok.json()["closure_status"] == "CLOSED"
    cl = client.get(f"/api/jobs/{jn}/closures", headers=h).json()[0]
    assert cl["action"] == "EXCEPTIONAL" and cl["approval_ref"].startswith("EXC-") and any(i["status"] == "FAIL" for i in cl["checklist"])


def test_activity_history_filters_pagination_export_and_immutability(h, world):
    jn = new_job(h)
    shipment = next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == jn)
    client.patch(f"/api/shipments/{shipment['id']}", json={"consignee": "Solvay SA", "commodity": "Soda ash"}, headers=h)
    page1 = events(h, jn, limit=2)
    assert len(page1["items"]) == 2 and page1["total"] >= 5
    page2 = client.get(f"/api/jobs/{jn}/activity", params={"limit": 2, "offset": 2}, headers=h).json()
    assert {e["id"] for e in page1["items"]}.isdisjoint({e["id"] for e in page2["items"]})
    hdrs = events(h, jn, category="header")["items"]
    assert hdrs and all(e["event_type"] in ("JOB_HEADER_CHANGED", "JOB_CREATED") for e in hdrs) and any(e["new_value"] == "Solvay SA" and e["previous_value"] in (None, "") for e in hdrs)
    assert events(h, jn, category="sellcost")["items"] and events(h, jn, category="buycost")["items"]
    assert events(h, jn, q="consignee")["total"] == 1
    csv = client.get(f"/api/jobs/{jn}/activity/export", headers=h)
    assert csv.status_code == 200 and "JOB_CREATED" in csv.text and "text/csv" in csv.headers["content-type"]
    assert any(e["event_type"] == "HISTORY_EXPORTED" for e in events(h, jn)["items"])
    # the database itself refuses to alter history
    db = SessionLocal()
    try:
        with pytest.raises(Exception):
            db.execute(text("UPDATE job_events SET description = 'tampered' WHERE job_number = :j"), {"j": jn})
            db.commit()
        db.rollback()
    finally:
        db.close()
    # another company (a second tenant with full rights) cannot read or touch it
    from sqlalchemy import select as sa_select

    from app.models import User
    from app.roles import ensure_role

    db2 = SessionLocal()
    try:
        u = db2.scalar(sa_select(User).where(User.username == world["demo"]))
        u.roles.append(ensure_role(db2, u.organization_id, "ERP_ADMIN"))
        db2.commit()
    finally:
        db2.close()
    other = hdr(world["demo"], portal="demo")
    assert client.get("/api/jobs/stages", headers=other).status_code == 200  # the other tenant has rights ...
    assert client.get(f"/api/jobs/{jn}/activity", headers=other).status_code == 404  # ... but sees nothing of this tenant's job
    assert client.get(f"/api/jobs/{jn}/lifecycle", headers=other).status_code == 404
    assert client.post(f"/api/jobs/{jn}/close", json=ACK, headers=other).status_code == 404
    assert client.get(f"/api/jobs/{jn}/activity/export", headers=other).status_code == 404
    # deleting an (open, never closed) job keeps its history
    assert client.delete(f"/api/shipments/{shipment['id']}", headers=h).status_code == 204
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT count(*) FROM job_events WHERE job_number = :j"), {"j": jn}).scalar() >= 5
    finally:
        db.close()
