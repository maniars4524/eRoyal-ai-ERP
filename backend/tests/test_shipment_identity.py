"""Single-entry shipment workflow: one booking, one forwarding job, one job per booked service, all sharing the shipment's details. A repeated click or retry never creates a second shipment."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def booking_body(services):
    return {"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Identity Exports", "carrier": "MSC", "carrier_booking_no": "ID1", "vessel": "MSC ROMA", "pol": "INMAA", "pod": "BEANR",
            "etd": str(date.today() + timedelta(days=10)), "eta": str(date.today() + timedelta(days=30)), "si_cutoff": str(date.today() + timedelta(days=1)), "vgm_cutoff": str(date.today() + timedelta(days=1)),
            "data": {"services": services, "container_type": "40HC", "container_qty": "2", "charges": []}}


def test_one_booking_gives_one_job_per_service_and_repeats_create_nothing_new(h):
    b = client.post("/api/bookings", json=booking_body("Freight, CHA, Transport"), headers=h).json()
    # a repeated confirm (double click, retry after a slow answer) must not create a second shipment
    first = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert first.status_code == 200, first.text
    again = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert again.status_code == 409
    # confirming creates the forwarding job and the customs job (when CHA is booked), once
    jobs = client.get(f"/api/bookings/{b['id']}/jobs", headers=h).json()
    assert sorted(j["job_type"] for j in jobs) == ["Customs", "Forwarding"] and first.json()["job_number"] in {j["job_number"] for j in jobs}
    # the other booked service job is made on request, once; a service that was not booked is refused
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "CHA"}, headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Transport"}, headers=h).status_code == 201
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Transport"}, headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Warehouse"}, headers=h).status_code == 422
    mine = [j for j in client.get("/api/shipments", headers=h).json() if j["booking_id"] == b["id"]]
    assert sorted(j["job_type"] for j in mine) == ["Customs", "Forwarding", "Transport"]
    assert len({j["job_number"] for j in mine}) == 3
    # the shipment's own details are shared by every job of the booking: route, mode, direction and the customer record
    assert {(j["pol"], j["pod"], j["mode"], j["direction"], j["customer_id"]) for j in mine} == {(mine[0]["pol"], mine[0]["pod"], "Sea", "Export", mine[0]["customer_id"])}


def test_a_draft_booking_cannot_make_jobs_and_a_cancelled_one_stays_cancelled(h):
    b = client.post("/api/bookings", json=booking_body("Freight, CHA"), headers=h).json()
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "CHA"}, headers=h).status_code == 409     # not confirmed yet
    assert [j for j in client.get("/api/shipments", headers=h).json() if j["booking_id"] == b["id"]] == []
    client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=h).status_code == 200
    assert client.post(f"/api/bookings/{b['id']}/confirm", headers=h).status_code == 409


def test_booking_edits_after_confirmation_are_refused_so_the_shipment_data_stays_consistent(h):
    b = client.post("/api/bookings", json=booking_body("Freight"), headers=h).json()
    client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert client.patch(f"/api/bookings/{b['id']}", json={"pol": "INBOM"}, headers=h).status_code == 409
