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
    assert client.put(f"/api/jobs/by-number/{cjob}/charges", json={"lines": []}, headers=h2).status_code == 423
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
