"""One editor per job: lock, conflict, read-only viewers, access requests, takeover, expiry, tabs, server-side refusal of writes."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import JobLock, Organization, User
from app.roles import ensure_role
from app.security import hash_password
from tests.test_bookings import client, hdr
from tests.test_cha_jobs import released


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


@pytest.fixture(scope="module")
def h2(world, admin2):
    return hdr(admin2["username"], admin2["license"])


@pytest.fixture(scope="module")
def ops(world):
    """A user without the take-over permission (Operations role)."""
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    u = User(email=f"ops-{tag}@users.test", username=f"ops{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name="Olive", last_name="Ops", organization_id=org.id, status="ACTIVE")
    u.roles.append(ensure_role(db, org.id, "OPERATIONS"))
    db.add(u)
    db.commit()
    out = {"username": u.username, "license": org.license_number}
    db.close()
    return hdr(out["username"], out["license"])


def tab():
    return {"tab_id": "tab-" + uuid.uuid4().hex[:8]}


def acquire(h, job, t, type_=""):
    return client.post(f"/api/job-locks/{job}/acquire", json={**t, "job_type": type_}, headers=h).json()


def test_first_editor_gets_the_lock_and_the_second_is_told_who_has_it(h, h2):
    b, done, fjob, cjob = released(h)
    t1, t2 = tab(), tab()
    a = acquire(h, cjob, t1)
    assert a["acquired"] and a["holder"]["job_type"] == "CHA" and a["holder"]["job_number"] == cjob and a["holder"]["started_at"]
    c = acquire(h2, cjob, t2)
    assert not c["acquired"] and c["holder"]["user_name"] == a["holder"]["user_name"] and c["holder"]["job_number"] == cjob
    st = client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()
    assert st["locked"] and not st["mine"] and st["holder"]["user_name"] == a["holder"]["user_name"]
    assert client.get(f"/api/job-locks/{fjob}/status", headers=h2).json()["locked"] is False      # a different job stays free
    assert acquire(h2, fjob, t2, "Freight")["acquired"]                                              # ... and editable by the other user at the same time


def test_the_server_refuses_writes_from_anyone_but_the_editor(h, h2):
    b, done, fjob, cjob = released(h)
    assert acquire(h, cjob, tab())["acquired"]
    r = client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1"}, headers=h2)
    assert r.status_code == 423, r.text
    d = r.json()["detail"]
    assert d["code"] == "JOB_LOCKED" and d["job_number"] == cjob and d["user_name"]
    assert client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "DOCUMENTATION"}, headers=h2).status_code == 423
    assert client.put(f"/api/shipments/by-number/{cjob}/charges", json={"lines": []}, headers=h2).status_code == 423
    assert client.post(f"/api/cha/files/{cjob}", json={"name": "a.pdf", "content_type": "application/pdf", "data_base64": "JVBERg=="}, headers=h2).status_code == 423
    assert client.get(f"/api/cha/jobs/{cjob}", headers=h2).status_code == 200                       # viewing stays open (read-only mode)
    assert client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1"}, headers=h).status_code == 200


def test_a_job_without_a_lock_is_not_blocked(h, h2):
    b, done, fjob, cjob = released(h)
    assert client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1"}, headers=h2).status_code == 200


def test_access_request_approved_hands_the_lock_over_and_notifies(h, h2):
    b, done, fjob, cjob = released(h)
    t1, t2 = tab(), tab()
    acquire(h, cjob, t1)
    assert not acquire(h2, cjob, t2)["acquired"]
    rq = client.post(f"/api/job-locks/{cjob}/request", headers=h2).json()
    assert rq["status"] == "PENDING"
    hb = client.post(f"/api/job-locks/{cjob}/heartbeat", json=t1, headers=h).json()
    assert hb["valid"] and len(hb["requests"]) == 1
    assert client.post(f"/api/job-locks/requests/{hb['requests'][0]['id']}/respond", json={"approve": True}, headers=h).status_code == 200
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["my_request"] == "APPROVED"
    n = client.post(f"/api/job-locks/{cjob}/heartbeat", json=t2, headers=h2).json()
    assert n["valid"] and any(x["event"] == "TRANSFERRED" for x in n["notices"])
    old = client.post(f"/api/job-locks/{cjob}/heartbeat", json=t1, headers=h).json()
    assert not old["valid"]                                                                          # the previous editor lost it and cannot release the new holder's lock
    client.post(f"/api/job-locks/{cjob}/release", json=t1, headers=h)
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["mine"]
    assert client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1"}, headers=h).status_code == 423


def test_request_can_be_declined(h, h2):
    b, done, fjob, cjob = released(h)
    acquire(h, cjob, tab())
    client.post(f"/api/job-locks/{cjob}/request", headers=h2)
    rid = client.post(f"/api/job-locks/{cjob}/heartbeat", json=tab(), headers=h).json()
    t = tab()
    acquire(h, cjob, t)
    rid = client.post(f"/api/job-locks/{cjob}/heartbeat", json=t, headers=h).json()["requests"][0]["id"]
    assert client.post(f"/api/job-locks/requests/{rid}/respond", json={"approve": False}, headers=h).status_code == 200
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["my_request"] == "DENIED"
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["holder"]["user_name"]


def test_takeover_needs_permission_and_a_reason_and_is_logged(h, h2, ops):
    b, done, fjob, cjob = released(h)
    acquire(h, cjob, tab())
    assert client.post(f"/api/job-locks/{cjob}/takeover", json={"reason": "urgent", **tab()}, headers=ops).status_code == 403
    assert client.post(f"/api/job-locks/{cjob}/takeover", json={"reason": "x", **tab()}, headers=h2).status_code == 422
    t = tab()
    assert client.post(f"/api/job-locks/{cjob}/takeover", json={"reason": "Customer waiting", **t}, headers=h2).json()["acquired"]
    ev = client.get(f"/api/job-locks/{cjob}/events", headers=h2).json()
    assert any(e["event"] == "TAKEOVER" and "Customer waiting" in e["detail"] for e in ev) and any(e["event"] == "ACQUIRED" for e in ev)
    assert client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1"}, headers=h).status_code == 423


def test_an_abandoned_lock_expires_and_is_logged(h, h2, world):
    b, done, fjob, cjob = released(h)
    acquire(h, cjob, tab())
    db = SessionLocal()
    row = db.scalar(select(JobLock).where(JobLock.job_number == cjob))
    row.tabs = {k: (v[:0] + "2020-01-01T00:00:00+00:00") for k, v in row.tabs.items()}
    db.commit()
    db.close()
    assert acquire(h2, cjob, tab())["acquired"]
    assert any(e["event"] == "EXPIRED" for e in client.get(f"/api/job-locks/{cjob}/events", headers=h2).json())


def test_closing_one_tab_keeps_the_lock_while_another_tab_of_the_same_user_is_open(h, h2):
    b, done, fjob, cjob = released(h)
    t1, t2 = tab(), tab()
    acquire(h, cjob, t1)
    assert acquire(h, cjob, t2)["acquired"]                      # same user, second tab: shares the lock
    client.post(f"/api/job-locks/{cjob}/release", json=t1, headers=h)
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["locked"]
    client.post(f"/api/job-locks/{cjob}/release", json=t2, headers=h)
    assert not client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["locked"]


def test_a_user_who_does_not_hold_the_lock_cannot_release_it(h, h2):
    b, done, fjob, cjob = released(h)
    t = tab()
    acquire(h, cjob, t)
    client.post(f"/api/job-locks/{cjob}/release", json=t, headers=h2)
    assert client.get(f"/api/job-locks/{cjob}/status", headers=h2).json()["locked"]


def test_five_users_edit_five_different_jobs_at_once(h, h2, ops, world, admin):
    jobs = [released(h)[3] for _ in range(3)]
    users = [h, h2, ops]
    for u, j in zip(users, jobs):
        assert acquire(u, j, tab())["acquired"]
    for u, j in zip(users, jobs):
        assert client.patch(f"/api/cha/jobs/{j}", json={"customs_location": "INMAA1"}, headers=u).status_code == 200
    # everyone else is refused on each job
    assert client.patch(f"/api/cha/jobs/{jobs[0]}", json={"customs_location": "INMAA1"}, headers=ops).status_code == 423


def draft_invoice(h, party="Lock Co"):
    return client.post("/api/documents", json={"doc_type": "invoice", "party_name": party, "lines": [{"charge_name": "x", "rate": "100", "tax_pct": "18"}]}, headers=h).json()


def test_an_invoice_is_locked_like_a_job(h, h2, ops):
    d = draft_invoice(h)
    key = f"INV:{d['id']}"
    t1 = tab()
    a = acquire(h, key, t1)
    assert a["acquired"] and a["holder"]["job_type"] == "Invoice" and a["holder"]["label"].startswith("Draft invoice")
    c = acquire(h2, key, tab())
    assert not c["acquired"] and c["holder"]["user_name"] == a["holder"]["user_name"]
    for method, url, body in [("patch", f"/api/documents/{d['id']}", {"remarks": "x"}), ("post", f"/api/documents/{d['id']}/post", None), ("post", f"/api/documents/{d['id']}/cancel", None)]:
        r = getattr(client, method)(url, headers=h2, **({"json": body} if body is not None else {}))
        assert r.status_code == 423 and r.json()["detail"]["code"] == "JOB_LOCKED" and "invoice" in r.json()["detail"]["message"], (url, r.text)
    assert client.get(f"/api/documents/{d['id']}", headers=h2).status_code == 200                 # viewing is open
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "by the editor"}, headers=h).status_code == 200
    other = draft_invoice(h2)
    assert client.patch(f"/api/documents/{other['id']}", json={"remarks": "mine"}, headers=h2).status_code == 200      # another invoice stays editable at the same time
    client.post(f"/api/job-locks/{key}/release", json=t1, headers=h)
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "now free"}, headers=h2).status_code == 200


def test_a_save_from_an_old_copy_is_refused(h, h2):
    d = draft_invoice(h)
    assert d["updated_at"]
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "first", "expected_updated_at": d["updated_at"]}, headers=h).status_code == 200
    r = client.patch(f"/api/documents/{d['id']}", json={"remarks": "second", "expected_updated_at": d["updated_at"]}, headers=h2)        # still holding the first copy
    assert r.status_code == 409 and "changed by someone else" in r.json()["detail"], r.text
    fresh = client.get(f"/api/documents/{d['id']}", headers=h2).json()
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "third", "expected_updated_at": fresh["updated_at"]}, headers=h2).status_code == 200
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "no stamp is still accepted"}, headers=h2).status_code == 200


def test_cha_job_stale_copy_is_refused(h, h2):
    b, done, fjob, cjob = released(h)
    job = client.get(f"/api/cha/jobs/{cjob}", headers=h).json()
    assert job["updated_at"]
    assert client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA1", "expected_updated_at": job["updated_at"]}, headers=h).status_code == 200
    r = client.patch(f"/api/cha/jobs/{cjob}", json={"customs_location": "INMAA4", "expected_updated_at": job["updated_at"]}, headers=h)
    assert r.status_code == 409 and "changed by someone else" in r.json()["detail"]


def test_five_accounts_users_on_the_same_and_different_invoices(h, h2, ops, world):
    from tests.conftest import PASSWORD  # noqa: F401
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    extra = []
    for i in range(2):
        tag = uuid.uuid4().hex[:6]
        u = User(email=f"acc{i}-{tag}@users.test", username=f"acc{i}{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name=f"Acc{i}", organization_id=org.id, status="ACTIVE")
        u.roles.append(ensure_role(db, org.id, "ERP_ADMIN"))
        db.add(u)
        db.commit()
        extra.append((u.username, org.license_number))
    db.close()
    users = [h, h2] + [hdr(u, lic) for u, lic in extra] + [ops]
    inv = [draft_invoice(h, f"Five {i}") for i in range(5)]
    # all five open the SAME invoice: exactly one becomes the editor, four are told who it is
    results = [acquire(u, f"INV:{inv[0]['id']}", tab()) for u in users]
    winners = [r for r in results if r["acquired"]]
    assert len(winners) == 1
    assert all(r["holder"]["user_name"] == winners[0]["holder"]["user_name"] for r in results)
    # five users each take a different invoice at the same time
    for u, d in zip(users[:5], inv):
        if d is inv[0]:
            continue
    mine = {}
    for u, d in zip(users, inv[1:] + [draft_invoice(h, "Six")]):
        r = acquire(u, f"INV:{d['id']}", tab())
        assert r["acquired"], r
        mine[d["id"]] = u
    for d_id, u in mine.items():
        assert client.patch(f"/api/documents/{d_id}", json={"remarks": "parallel"}, headers=u).status_code == 200
    # nobody else can write another person's invoice
    first_id = next(iter(mine))
    intruder = next(u for u in users if u is not mine[first_id])
    assert client.patch(f"/api/documents/{first_id}", json={"remarks": "x"}, headers=intruder).status_code == 423


def test_master_record_audit_log_lists_field_changes(h):
    r = client.post("/api/masters/branch", json={"code": "AUD1", "name": "Audit Branch", "data": {"city": "Chennai", "branch_type": "Head Office"}}, headers=h)
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert client.patch(f"/api/masters/branch/{bid}", json={"name": "Audit Branch 2", "data": {"city": "Mumbai", "branch_type": "Head Office"}}, headers=h).status_code == 200
    log = client.get(f"/api/masters/branch/{bid}/audit", headers=h).json()
    fields = {(x["field"], x["from"], x["to"]) for x in log if x["action"] == "Updated"}
    assert ("Name", "Audit Branch", "Audit Branch 2") in fields and ("City", "Chennai", "Mumbai") in fields, log
    assert any(x["action"] == "Created" for x in log) and all(x["user"] and x["when"] for x in log)


def test_a_quotation_is_locked_like_a_job_and_logout_releases_every_lock(h, h2, world, admin):
    q = client.post("/api/quotations", json={"party_name": "Lock Party", "mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "BEANR", "lines": [{"side": "SELL", "charge_name": "Docs", "ccy": "INR", "exch": "1", "rate": "100", "qty": "1", "basis": "Shipment"}]}, headers=h)
    assert q.status_code == 201, q.text
    qid = q.json()["id"]
    key = f"QT:{qid}"
    a = acquire(h, key, tab())
    assert a["acquired"] and a["holder"]["job_type"] == "Quotation" and a["holder"]["label"]
    assert not acquire(h2, key, tab())["acquired"]
    r = client.patch(f"/api/quotations/{qid}", json={"remarks": "x"}, headers=h2)
    assert r.status_code == 423 and "quotation" in r.json()["detail"]["message"]
    assert client.get(f"/api/quotations/{qid}", headers=h2).status_code == 200                       # viewing the saved details is open
    assert client.patch(f"/api/quotations/{qid}", json={"remarks": "by the editor"}, headers=h).status_code == 200
    # an approver acting on it is not editing: the approval routes are not blocked by the lock
    assert client.post(f"/api/quotations/{qid}/approval/approve", headers=h2).status_code != 423
    # signing out releases the editor's locks at once
    mine = hdr(admin["username"], admin["license"])
    k2 = f"INV:{draft_invoice(mine)['id']}"
    acquire(mine, k2, tab())
    assert client.get(f"/api/job-locks/{k2}/status", headers=h2).json()["locked"]
    assert client.post("/api/auth/logout", headers=mine).status_code == 200
    assert not client.get(f"/api/job-locks/{k2}/status", headers=h2).json()["locked"]
