"""Masters > Inventory: the 21 inventory reference masters use the generic master framework. They start empty (nothing seeded), reject duplicate codes, keep their own list per type and write an audit trail."""
import uuid

import pytest

from app.masters_api import MASTER_TYPES
from tests.test_bookings import client, hdr

INV = sorted(k for k in MASTER_TYPES if k.startswith("inv_"))


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def test_all_21_inventory_types_are_exposed():
    assert len(INV) == 21
    assert {"inv_depot", "inv_yard_location", "inv_item", "inv_axle_config", "inv_tyre_brand"} <= set(INV)


def test_lists_start_empty_nothing_is_seeded(h):
    for t in INV:
        assert client.get(f"/api/masters/{t}", headers=h).json() == [], t


def test_each_type_creates_lists_rejects_duplicates_and_audits(h):
    tag = uuid.uuid4().hex[:5].upper()
    for t in INV:
        body = {"code": f"T{tag}", "name": f"{t} {tag}", "status": "ACTIVE", "data": {"remarks": "x", "total_capacity_teu": "120"}}
        r = client.post(f"/api/masters/{t}", json=body, headers=h)
        assert r.status_code == 201, (t, r.text)
        assert client.post(f"/api/masters/{t}", json=body, headers=h).status_code == 409, t
        rows = client.get(f"/api/masters/{t}", headers=h).json()
        assert [x["code"] for x in rows] == [f"T{tag}"], t
        up = client.patch(f"/api/masters/{t}/{r.json()['id']}", json={"name": f"changed {tag}", "data": {"remarks": "y"}}, headers=h)
        assert up.status_code == 200, (t, up.text)
        assert any(a["action"] for a in client.get(f"/api/masters/{t}/{r.json()['id']}/audit", headers=h).json()), t


def test_the_same_code_in_two_different_masters_is_fine(h):
    assert client.post("/api/masters/inv_depot", json={"code": "SHARED1", "name": "Depot"}, headers=h).status_code == 201
    assert client.post("/api/masters/inv_warehouse", json={"code": "SHARED1", "name": "Store"}, headers=h).status_code == 201


def test_not_readable_without_signing_in():
    assert client.get("/api/masters/inv_depot").status_code in (401, 403)
