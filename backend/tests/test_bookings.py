import threading
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.billing import fiscal_year
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


def draft(h, **extra):
    body = {"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Acme Exports", "carrier": "MSC", "carrier_booking_no": "MSC123", "vessel": "MSC ROMA / 254W",
            "pol": "INMAA", "pod": "BEANR", "etd": str(date.today() + timedelta(days=10)), "eta": str(date.today() + timedelta(days=30)), "si_cutoff": str(date.today() + timedelta(days=1)), "vgm_cutoff": str(date.today() + timedelta(days=2)),
            "data": {"pcs": "120", "gwt": "24000", "consignee": "Solvay SA", "marks": "Soda ash", "charges": [{"type": "AP", "code": "OFR", "qty": "2", "rate": "680", "currency": "USD"}]}, **extra}
    r = client.post("/api/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_draft_roundtrip_and_validation(h):
    b = draft(h)
    assert b["status"] == "DRAFT" and b["number"] is None and b["data"]["charges"][0]["code"] == "OFR"
    assert client.post("/api/bookings", json={"mode": "Teleport"}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"customer_id": 99999999}, headers=h).status_code == 404
    assert client.post("/api/bookings", json={"etd": "2026-12-10", "eta": "2026-12-01"}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"data": {"Bad Key": 1}}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"data": {"nested": {"a": {"b": 1}}}}, headers=h).status_code == 422
    upd = client.patch(f"/api/bookings/{b['id']}", json={"carrier": "ONE", "etd": None, "data": {"pcs": "5"}}, headers=h)
    assert upd.status_code == 200 and upd.json()["carrier"] == "ONE" and upd.json()["etd"] is None and upd.json()["data"] == {"pcs": "5"}


def test_confirm_numbers_creates_the_job_and_locks(h):
    b = draft(h)
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert done.status_code == 200, done.text
    body = done.json()
    fy = fiscal_year(date.today())
    assert body["status"] == "CONFIRMED" and body["number"].startswith(f"BKG/{fy}/") and body["job_number"].startswith("SEAEXP-")
    job = next(j for j in client.get("/api/shipments", headers=h).json() if j["job_number"] == body["job_number"])
    assert job["mode"] == "Sea" and job["pol"] == "INMAA" and job["pod"] == "BEANR" and job["container_mode"] == "FCL" and job["consignee"] == "Solvay SA" and job["packages"] == 120 and job["status"] == "Booked"
    assert client.patch(f"/api/bookings/{b['id']}", json={"carrier": "x"}, headers=h).status_code == 409
    assert client.delete(f"/api/bookings/{b['id']}", headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/confirm", headers=h).status_code == 409


def test_confirm_requires_the_key_fields(h):
    empty = client.post("/api/bookings", json={}, headers=h).json()
    r = client.post(f"/api/bookings/{empty['id']}/confirm", headers=h)
    assert r.status_code == 422 and "carrier" in r.json()["detail"] and "port of loading" in r.json()["detail"]


def test_numbers_are_gap_free_even_under_concurrency(h):
    ids = [draft(h)["id"] for _ in range(6)]
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(TestClient(app).post(f"/api/bookings/{i}/confirm", headers=h).json()["number"])) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 6
    seq = sorted(int(n.rsplit("/", 1)[1]) for n in out)
    assert seq == list(range(seq[0], seq[0] + 6))


def test_cancel_release_space_and_summary(h):
    a, b = draft(h), draft(h, si_cutoff=str(date.today() + timedelta(days=1)))
    client.post(f"/api/bookings/{a['id']}/confirm", headers=h)
    client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert client.post(f"/api/bookings/{a['id']}/cancel", headers=h).json()["status"] == "CANCELLED"
    assert client.post(f"/api/bookings/{b['id']}/release-space", headers=h).json()["status"] == "SPACE_RELEASED"
    assert client.post(f"/api/bookings/{a['id']}/cancel", headers=h).status_code == 409
    assert client.post(f"/api/bookings/{draft(h)['id']}/cancel", headers=h).status_code == 409
    s = client.get("/api/bookings/summary", headers=h).json()
    assert s["space_released"] >= 1 and s["confirmed_this_week"] >= 1 and s["si_cutoff_48h"] >= 1 and s["vgm_pending"] >= 1
    assert [x["id"] for x in client.get("/api/bookings?status=SPACE_RELEASED", headers=h).json()][0] == b["id"] or b["id"] in [x["id"] for x in client.get("/api/bookings?status=SPACE_RELEASED", headers=h).json()]


def test_duplicate_creates_a_clean_draft(h):
    src = draft(h)
    client.post(f"/api/bookings/{src['id']}/confirm", headers=h)
    copy = client.post(f"/api/bookings/{src['id']}/duplicate", headers=h)
    assert copy.status_code == 201
    c = copy.json()
    assert c["status"] == "DRAFT" and c["number"] is None and c["job_number"] is None and c["carrier_booking_no"] is None and c["pol"] == "INMAA" and c["data"]["consignee"] == "Solvay SA"


def test_draft_delete_and_search(h):
    d = draft(h, party_name="Zebra Searchable Co")
    assert [x["id"] for x in client.get("/api/bookings?q=zebra", headers=h).json()] == [d["id"]]
    assert client.delete(f"/api/bookings/{d['id']}", headers=h).status_code == 204
    assert client.get(f"/api/bookings/{d['id']}", headers=h).status_code == 404


def test_tenant_isolation_and_permissions(world, admin, h):
    b = draft(h)
    assert client.get(f"/api/bookings/{b['id']}", headers=hdr(world["demo"], portal="demo")).status_code in (403, 404)
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER", "OPERATIONS"):
        client.post("/api/users", json={"username": f"{role[:2].lower()}{tag}", "email": f"{role[:2].lower()}{tag}@b.test", "password": PASSWORD, "first_name": role, "role": role}, headers=h)
    viewer, ops = hdr(f"vi{tag}", admin["license"]), hdr(f"op{tag}", admin["license"])
    assert client.get(f"/api/bookings/{b['id']}", headers=viewer).status_code == 200
    assert client.post("/api/bookings", json={}, headers=viewer).status_code == 403
    assert client.post(f"/api/bookings/{b['id']}/confirm", headers=viewer).status_code == 403
    assert client.post("/api/bookings", json={}, headers=ops).status_code == 201
    assert client.get("/api/bookings").status_code == 401
    assert client.get("/api/bookings", headers=hdr(world["dev"], portal="developer")).status_code == 403
