"""A sub job exists only when the user chooses to create one under a real job."""
import pytest

from tests.test_bookings import client, hdr
from tests.test_job_details import job


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def body(**kw):
    return {"mode": "Air", "direction": "Import", "container_mode": "Air", "pol": "FRA", "pod": "MAA", **kw}


def test_a_job_is_standalone_unless_a_parent_is_chosen(h):
    r = client.post("/api/shipments", json=body(), headers=h)
    assert r.status_code == 201 and r.json().get("parent_job_id") is None


def test_a_sub_job_points_at_its_parent(h):
    parent = job(h)
    r = client.post("/api/shipments", json=body(parent_job_number=parent), headers=h)
    assert r.status_code == 201, r.text
    pid = next(x["id"] for x in client.get("/api/shipments", headers=h).json() if x["job_number"] == parent)
    assert r.json()["parent_job_id"] == pid


def test_a_missing_or_cancelled_parent_is_refused(h):
    assert client.post("/api/shipments", json=body(parent_job_number="NOPE-1"), headers=h).status_code == 422
    parent = job(h)
    client.post(f"/api/jobs/{parent}/cancel", json={"reason": "Cancelled for the sub job test"}, headers=h)
    assert client.post("/api/shipments", json=body(parent_job_number=parent), headers=h).status_code == 409
