"""Shipment templates: admin-configured combinations that pre-fill the New Shipment / Booking selection page."""
import pytest

from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def tpl(name, **d):
    return {"code": name[:20].upper().replace(" ", ""), "name": name, "data": d}


def test_the_list_starts_empty_and_the_sample_is_loaded_only_on_request(h):
    assert client.get("/api/masters/shipment_template", headers=h).json() == []
    r = client.post("/api/masters/shipment_template/import-sample", headers=h)
    assert r.status_code == 201 and r.json()["added"] == 16 + 8 + 5 + 10
    rows = client.get("/api/masters/shipment_template", headers=h).json()
    assert any(x["name"] == "Sea Export FCL HBL – MTO" and x["data"]["house_bl_type"] == "MTO" and x["data"]["booking_type"] == "House" for x in rows)
    assert not any(x["data"]["shipment_mode"] == "Air" and x["data"]["house_bl_type"] for x in rows)             # air never carries a House BL type
    again = client.post("/api/masters/shipment_template/import-sample", headers=h).json()
    assert again["added"] == 0                                                                                    # idempotent


def test_a_house_bl_type_is_refused_outside_sea_house(h):
    bad = client.post("/api/masters/shipment_template", json=tpl("Air with MTO", shipment_mode="Air", direction="Export", booking_type="House", house_bl_type="MTO"), headers=h)
    assert bad.status_code == 422 and "Sea" in bad.json()["detail"]
    assert client.post("/api/masters/shipment_template", json=tpl("Nonsense", shipment_mode="Boat"), headers=h).status_code == 422
    ok = client.post("/api/masters/shipment_template", json=tpl("Own Sea LCL", shipment_mode="Sea", direction="Export", shipment_type="LCL", booking_type="House", house_bl_type="OTL"), headers=h)
    assert ok.status_code == 201
    assert client.patch(f"/api/masters/shipment_template/{ok.json()['id']}", json={"data": {"shipment_mode": "Air", "booking_type": "House", "house_bl_type": "OTL"}}, headers=h).status_code == 422
