"""Notification centre: one list from many providers, each only for what the user may see."""
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def titles(h):
    return [x["title"] for x in client.get("/api/notifications", headers=h).json()["items"]]


def test_an_internal_message_appears_and_can_be_withdrawn(h):
    r = client.post("/api/notifications/messages", json={"title": "Office closed on Friday", "body": "Public holiday"}, headers=h)
    assert r.status_code == 201
    got = client.get("/api/notifications", headers=h).json()
    msg = next(x for x in got["items"] if x["title"] == "Office closed on Friday")
    assert msg["category"] == "Messages" and "Public holiday" in msg["text"] and got["counts"]["Messages"] >= 1
    assert client.delete(f"/api/notifications/messages/{r.json()['id']}", headers=h).status_code == 200
    assert "Office closed on Friday" not in titles(h)


def test_an_expired_message_is_not_shown(h):
    y = (date.today() - timedelta(days=1)).isoformat()
    client.post("/api/notifications/messages", json={"title": "Old notice here", "expires_on": y}, headers=h)
    assert "Old notice here" not in titles(h)


def test_pending_invoices_and_due_purchase_bills_are_listed(h):
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Acme Notify", "lines": []}, headers=h)
    assert d.status_code == 201, d.text
    got = client.get("/api/notifications", headers=h).json()
    assert any(x["category"] == "Approvals" and "Invoice approval pending" in x["title"] and "Acme Notify" in x["text"] for x in got["items"])
    assert set(got["counts"]) == {"Shipments", "Approvals", "Payments", "People", "Messages"}


def test_routing_alerts_are_part_of_the_list(h):
    j = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "SGSIN"}, headers=h).json()["job_number"]
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post(f"/api/jobs/{j}/routing/legs", json={"carrier": "MSC Notify", "voyage": "V1", "pol": "Chennai", "pod": "Singapore", "etd": soon, "stage": "PLANNED", "follow_up": True}, headers=h)
    got = client.get("/api/notifications", headers=h).json()["items"]
    row = next(x for x in got if x["category"] == "Shipments" and "MSC Notify" in x["title"])
    assert row["link"] == {"go": "job-detail", "jobNo": j, "tab": "routing"} and row["severity"] <= 1


def test_a_user_without_the_rights_sees_only_what_they_may(h, world):
    from tests.test_containers import make_user

    v = make_user(world, "nview", ["shipment.read"])
    hv = hdr(v["username"], v["license"])
    mine = client.get("/api/notifications", headers=hv).json()["items"]
    assert not any("Invoice approval" in x["title"] for x in mine)                         # needs billing.read
    assert not any(x["category"] == "Approvals" for x in mine)
    assert client.post("/api/notifications/messages", json={"title": "I should not post"}, headers=hv).status_code == 403


def test_settings_have_defaults_and_are_validated(h):
    got = client.get("/api/notifications/settings", headers=h).json()
    assert got["settings"]["routing"]["etd_lead_value"] == 5 and got["settings"]["routing"]["etd_lead_unit"] == "days" and len(got["roles"]) >= 1
    assert client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 0}}, headers=h).status_code == 422
    assert client.put("/api/notifications/settings", json={"routing": {"etd_lead_unit": "weeks"}}, headers=h).status_code == 422
    assert client.put("/api/notifications/settings", json={"routing": {"nonsense": 1}}, headers=h).status_code == 422
    assert client.put("/api/notifications/settings", json={"audience": {"Bogus": {"enabled": True}}}, headers=h).status_code == 422
    ok = client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 10, "etd_lead_unit": "days"}}, headers=h)
    assert ok.status_code == 200 and ok.json()["settings"]["routing"]["etd_lead_value"] == 10
    client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 5}}, headers=h)


def test_the_etd_lead_time_decides_when_it_is_announced(h):
    j = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "SGSIN"}, headers=h).json()["job_number"]
    etd = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    client.post(f"/api/jobs/{j}/routing/legs", json={"carrier": "Lead Test Ship", "voyage": "L1", "pol": "Chennai", "pod": "Singapore", "etd": etd, "stage": "CONFIRMED"}, headers=h)
    has = lambda: any("Lead Test Ship" in x["title"] for x in client.get("/api/notifications", headers=h).json()["items"])
    client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 5, "etd_lead_unit": "days"}}, headers=h)
    assert not has()                                                                           # 8 days away, only 5 days before
    client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 10, "etd_lead_unit": "days"}}, headers=h)
    assert has()                                                                               # 10 days before: now announced
    client.put("/api/notifications/settings", json={"routing": {"etd_lead_value": 5}}, headers=h)


def test_who_receives_each_kind_is_a_setting(h, world):
    from tests.test_containers import make_user

    v = make_user(world, "naud", ["shipment.read", "billing.read"])
    hv = hdr(v["username"], v["license"])
    client.post("/api/notifications/messages", json={"title": "Managers only message", "audience_roles": ["Manager"]}, headers=h)
    client.post("/api/notifications/messages", json={"title": "Everyone message here"}, headers=h)
    mine = [x["title"] for x in client.get("/api/notifications", headers=hv).json()["items"]]
    assert "Everyone message here" in mine and "Managers only message" not in mine
    assert "Managers only message" in titles(h)                                                # the admin sees it
    client.put("/api/notifications/settings", json={"audience": {"Messages": {"enabled": False, "roles": []}}}, headers=h)
    assert not any(x["category"] == "Messages" for x in client.get("/api/notifications", headers=hv).json()["items"])
    client.put("/api/notifications/settings", json={"audience": {"Messages": {"enabled": True, "roles": []}}}, headers=h)
