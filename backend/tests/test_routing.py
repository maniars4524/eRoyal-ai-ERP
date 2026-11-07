"""Job routing: legs per job (flights / vessels), planned vs confirmed, follow-up, history, alerts, and the job itself left untouched."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def mk(h, mode="Sea", direction="Export", **kw):
    r = client.post("/api/shipments", json={"mode": mode, "direction": direction, "container_mode": "FCL" if mode == "Sea" else "Air", "pol": "INMAA", "pod": "SGSIN", "etd": "2030-05-01", "eta": "2030-05-20", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["job_number"]


def iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def sea(**kw):
    base = {"carrier": "MSC Orion", "voyage": "VOY12345", "pol": "Chennai", "pod": "Singapore", "etd": iso(5), "eta": iso(12), "stage": "PLANNED"}
    base.update(kw)
    return base


def test_a_job_with_no_routing_suggests_a_first_leg_from_the_job_and_writes_nothing(h):
    j = mk(h)
    r = client.get(f"/api/jobs/{j}/routing", headers=h).json()
    assert r["kind"] == "SEA" and r["legs"] == [] and r["suggested"] and r["suggested"][0]["pol"] == "INMAA" and r["suggested"][0]["etd"].startswith("2030-05-01")
    assert client.get(f"/api/jobs/{j}/routing", headers=h).json()["legs"] == []


def test_sea_legs_with_transshipment_in_order_and_independent_stages(h):
    j = mk(h)
    a = client.post(f"/api/jobs/{j}/routing/legs", json=sea(stage="CONFIRMED"), headers=h)
    b = client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier="Maersk Bella", voyage="VOY67890", pol="Singapore", pod="Rotterdam", etd=iso(14), eta=iso(30)), headers=h)
    assert a.status_code == b.status_code == 201, a.text + b.text
    legs = client.get(f"/api/jobs/{j}/routing", headers=h).json()["legs"]
    assert [x["seq"] for x in legs] == [1, 2] and [x["stage"] for x in legs] == ["CONFIRMED", "PLANNED"]
    row = next(x for x in client.get("/api/shipments", headers=h).json() if x["job_number"] == j)
    assert row["pol"] == "INMAA" and row["pod"] == "SGSIN"


def test_air_legs_need_an_airline_or_flight_and_have_no_ports(h):
    j = mk(h, "Air", "Import")
    assert client.post(f"/api/jobs/{j}/routing/legs", json={"etd": iso(1)}, headers=h).status_code == 422
    r = client.post(f"/api/jobs/{j}/routing/legs", json={"carrier": "Emirates", "voyage": "EK404", "pol": "DXB", "etd": iso(2), "eta": iso(3)}, headers=h)
    assert r.status_code == 201 and r.json()["kind"] == "AIR" and r.json()["pol"] is None


def test_validation(h):
    j = mk(h)
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(etd=iso(10), eta=iso(5)), headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(stage="MAYBE"), headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(pol="Chennai", pod="chennai"), headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier=None), headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(stage="CONFIRMED", etd=None, eta=None), headers=h).status_code == 422
    road = mk(h, "Road", "Export")
    assert client.get(f"/api/jobs/{road}/routing", headers=h).json()["applicable"] is False
    assert client.post(f"/api/jobs/{road}/routing/legs", json=sea(), headers=h).status_code == 422


def test_changes_are_kept_in_history_and_the_activity_log(h):
    j = mk(h)
    leg = client.post(f"/api/jobs/{j}/routing/legs", json=sea(), headers=h).json()
    r = client.put(f"/api/jobs/{j}/routing/legs/{leg['id']}", json=sea(stage="CONFIRMED", etd=iso(6)), headers=h)
    assert r.status_code == 200 and r.json()["stage"] == "CONFIRMED"
    hist = client.get(f"/api/jobs/{j}/routing/history", headers=h).json()
    assert any(x["field"] == "Stage" and x["from"] == "PLANNED" and x["to"] == "CONFIRMED" for x in hist)
    assert any(x["field"] == "ETD" for x in hist) and any(x["action"] == "CREATED" for x in hist)
    assert "ROUTING_UPDATED" in str(client.get(f"/api/jobs/{j}/activity", headers=h).json())


def test_removing_a_leg_needs_a_reason_and_keeps_it_in_history(h):
    j = mk(h)
    leg = client.post(f"/api/jobs/{j}/routing/legs", json=sea(), headers=h).json()
    assert client.delete(f"/api/jobs/{j}/routing/legs/{leg['id']}", headers=h).status_code == 422
    assert client.delete(f"/api/jobs/{j}/routing/legs/{leg['id']}?reason=Booked on another ship", headers=h).status_code == 200
    assert client.get(f"/api/jobs/{j}/routing", headers=h).json()["legs"] == []
    assert any(x["action"] == "REMOVED" and x["remarks"] == "Booked on another ship" for x in client.get(f"/api/jobs/{j}/routing/history", headers=h).json())


def test_alerts_follow_the_legs(h):
    j = mk(h)
    plain = client.post(f"/api/jobs/{j}/routing/legs", json=sea(stage="CONFIRMED", etd=iso(40), eta=iso(60)), headers=h).json()
    fu = client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier="Maersk Bella", stage="PLANNED", follow_up=True, etd=iso(41), eta=iso(61)), headers=h).json()
    soon = client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier="ONE Apus", stage="CONFIRMED", etd=iso(2), eta=iso(25)), headers=h).json()
    planned = client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier="Evergreen", etd=iso(5), eta=iso(20)), headers=h).json()
    by = {a["leg_id"]: a for a in client.get("/api/routing/alerts", headers=h).json()}
    assert plain["id"] not in by
    assert "FOLLOW_UP" in by[fu["id"]]["flags"] and by[fu["id"]]["job_number"] == j
    assert "ETD" in by[soon["id"]]["flags"]
    assert "PLANNED" in by[planned["id"]]["flags"]
    done = client.post(f"/api/jobs/{j}/routing/legs/{fu['id']}/follow-up", json={"status": "DONE", "note": "Carrier confirmed"}, headers=h)
    assert done.status_code == 200
    assert fu["id"] not in {a["leg_id"] for a in client.get("/api/routing/alerts", headers=h).json()}
    c = client.post(f"/api/jobs/{j}/routing/legs", json=sea(carrier="Hapag", stage="CONFIRMED", follow_up=True, etd=iso(50), eta=iso(70)), headers=h).json()
    assert c["follow_up"] is False                                                                    # follow-up is for planned legs only
    assert client.post(f"/api/jobs/{j}/routing/legs/{plain['id']}/follow-up", json={"status": "DONE"}, headers=h).status_code == 409
    client.put(f"/api/jobs/{j}/routing/legs/{planned['id']}", json=sea(carrier="Evergreen", stage="CONFIRMED", etd=iso(5), eta=iso(20)), headers=h)
    flags = {f for a in client.get("/api/routing/alerts", headers=h).json() if a["leg_id"] == planned["id"] for f in a["flags"]}
    assert "PLANNED" not in flags


def test_a_cancelled_job_is_not_editable_and_raises_no_alerts(h):
    j = mk(h)
    leg = client.post(f"/api/jobs/{j}/routing/legs", json=sea(), headers=h).json()
    client.post(f"/api/jobs/{j}/cancel", json={"reason": "Cancelled for the routing test"}, headers=h)
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(), headers=h).status_code == 409
    assert leg["id"] not in {a["leg_id"] for a in client.get("/api/routing/alerts", headers=h).json()}


def test_a_viewer_can_read_but_not_change(h, world):
    from tests.test_containers import make_user

    v = make_user(world, "rview", ["shipment.read"])
    j = mk(h)
    hv = hdr(v["username"], v["license"])
    assert client.get(f"/api/jobs/{j}/routing", headers=hv).status_code == 200
    assert client.post(f"/api/jobs/{j}/routing/legs", json=sea(), headers=hv).status_code == 403


def test_the_first_leg_is_suggested_from_the_ports_even_with_no_vessel_or_dates(h):
    r = client.post("/api/shipments", json={"mode": "Sea", "direction": "Import", "container_mode": "FCL", "pol": "SGSIN(SINGAPORE)", "pod": "INMAA(CHENNAI)"}, headers=h)
    j = r.json()["job_number"]
    got = client.get(f"/api/jobs/{j}/routing", headers=h).json()
    assert got["suggested"] and got["suggested"][0]["pol"] == "SGSIN(SINGAPORE)" and got["suggested"][0]["pod"] == "INMAA(CHENNAI)" and got["suggested"][0]["carrier"] is None
    air = client.post("/api/shipments", json={"mode": "Air", "direction": "Import", "container_mode": "Air", "pol": "SIN", "pod": "MAA"}, headers=h).json()["job_number"]
    assert client.get(f"/api/jobs/{air}/routing", headers=h).json()["suggested"] == []            # air legs carry no ports; nothing else is known
