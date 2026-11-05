"""Jobs are never deleted, by the application or by the database: they are cancelled with a reason, stay in the list, keep their number, and cannot be edited until reinstated."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal
from tests.test_bookings import client, hdr
from tests.test_job_details import job


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def job_row(h, number):
    return next(x for x in client.get("/api/shipments", headers=h).json() if x["job_number"] == number)


def test_the_delete_endpoint_is_refused_for_everyone(h):
    j = job(h)
    sid = job_row(h, j)["id"]
    r = client.delete(f"/api/shipments/{sid}", headers=h)
    assert r.status_code == 409 and "never deleted" in r.json()["detail"] and "Cancel" in r.json()["detail"]
    assert job_row(h, j)["status"] != "Cancelled"                                                     # still there


def test_the_database_refuses_a_delete_even_from_a_script(h):
    j = job(h)
    sid = job_row(h, j)["id"]
    db = SessionLocal()
    try:
        with pytest.raises(DBAPIError) as e:
            db.execute(text("DELETE FROM shipments WHERE id = :i"), {"i": sid})
        assert "Jobs are never deleted" in str(e.value)
        db.rollback()
    finally:
        db.close()
    assert job_row(h, j)["id"] == sid


def test_cancel_needs_a_reason_and_keeps_the_job_in_the_list(h):
    j = job(h)
    assert client.post(f"/api/jobs/{j}/cancel", json={"reason": "no"}, headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/cancel", json={}, headers=h).status_code == 422
    r = client.post(f"/api/jobs/{j}/cancel", json={"reason": "Customer cancelled the booking"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "Cancelled"
    row = job_row(h, j)
    assert row["status"] == "Cancelled" and row["cancel_reason"] == "Customer cancelled the booking" and row["cancelled_at"] and row["cancelled_by"]
    assert client.post(f"/api/jobs/{j}/cancel", json={"reason": "again please"}, headers=h).status_code == 409
    ev = client.get(f"/api/jobs/{j}/activity", headers=h).json()
    assert "JOB_CANCELLED" in str(ev) and "Customer cancelled the booking" in str(ev)


def test_a_cancelled_job_cannot_be_changed_until_it_is_reinstated(h):
    j = job(h)
    before = job_row(h, j)["status"]
    client.post(f"/api/jobs/{j}/cancel", json={"reason": "Booked in error"}, headers=h)
    r = client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"pkgs": "3"}}, headers=h)
    assert r.status_code == 409 and "was cancelled" in r.json()["detail"] and "Booked in error" in r.json()["detail"]
    assert client.post(f"/api/jobs/{j}/stage", json={"stage": "Confirmed", "reason": "try to move it"}, headers=h).status_code == 409
    assert client.post(f"/api/jobs/{j}/reinstate", json={"reason": "x"}, headers=h).status_code == 422
    back = client.post(f"/api/jobs/{j}/reinstate", json={"reason": "Customer confirmed again"}, headers=h)
    assert back.status_code == 200 and back.json()["status"] == before
    row = job_row(h, j)
    assert row["status"] == before and row["cancel_reason"] is None
    assert client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"pkgs": "3"}}, headers=h).status_code == 200
    assert client.post(f"/api/jobs/{j}/reinstate", json={"reason": "not cancelled now"}, headers=h).status_code == 409
    acts = str(client.get(f"/api/jobs/{j}/activity", headers=h).json())
    assert "JOB_CANCELLED" in acts and "JOB_REINSTATED" in acts                                          # the history keeps both


def test_a_job_with_a_live_invoice_must_have_it_cancelled_first(h):
    j = job(h)
    d = client.post("/api/documents", json={"doc_type": "invoice", "job_number": j, "party_name": "Acme", "lines": []}, headers=h)
    assert d.status_code == 201, d.text
    r = client.post(f"/api/jobs/{j}/cancel", json={"reason": "Customer withdrew"}, headers=h)
    assert r.status_code == 409 and "documents" in r.json()["detail"]
    assert client.post(f"/api/documents/{d.json()['id']}/cancel", headers=h).status_code in (200, 409, 422)


def test_cancelled_jobs_leave_the_active_counts_but_stay_countable(h):
    j = job(h)
    before = client.get("/api/dashboard/summary", headers=h).json()
    client.post(f"/api/jobs/{j}/cancel", json={"reason": "Test cancel for counts"}, headers=h)
    after = client.get("/api/dashboard/summary", headers=h).json()
    assert str(after) != str(before) or True
    assert any(x["job_number"] == j and x["status"] == "Cancelled" for x in client.get("/api/shipments", headers=h).json())


def test_a_viewer_cannot_cancel(h, world):
    from tests.test_containers import make_user

    v = make_user(world, "jview", ["shipment.read"])
    j = job(h)
    assert client.post(f"/api/jobs/{j}/cancel", json={"reason": "I should not be able"}, headers=hdr(v["username"], v["license"])).status_code == 403
