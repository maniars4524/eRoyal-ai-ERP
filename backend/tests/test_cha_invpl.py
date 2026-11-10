"""CHA > Invoice & Packing List: records are kept per company, totals are computed on the server, and "Get Bill" starts one from a job."""
import pytest

from tests.test_bookings import client, hdr
from tests.test_job_details import job


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def body(no="INV-1"):
    return {"invoice_no": no, "invoice_date": "2026-11-10", "data": {"exporter": "PSR EXPORTERS\n2 C Park, Mumbai", "consignee": "SYED TRADING", "currency": "USD", "cur_value": "74.45",
            "lines": [{"pkgs": "10", "description": "GARMENTS", "qty": "2", "rate": "200", "net_wt": "2"}, {"pkgs": "5", "description": "SHIRTS", "qty": "3", "rate": "10"}]}}


def test_create_list_update_delete(h):
    r = client.post("/api/cha/invoice-packing", json=body(), headers=h)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["invoice_no"] == "INV-1" and rec["exporter"] == "PSR EXPORTERS" and rec["total"] == "430.00" and rec["currency"] == "USD"
    assert client.get(f"/api/cha/invoice-packing/{rec['id']}", headers=h).json()["data"]["lines"][0]["description"] == "GARMENTS"
    assert any(x["id"] == rec["id"] for x in client.get("/api/cha/invoice-packing?q=PSR", headers=h).json()["items"])
    b = body("INV-1B"); b["data"]["lines"][0]["qty"] = "3"
    up = client.put(f"/api/cha/invoice-packing/{rec['id']}", json=b, headers=h).json()
    assert up["invoice_no"] == "INV-1B" and up["total"] == "630.00"
    assert client.delete(f"/api/cha/invoice-packing/{rec['id']}", headers=h).status_code == 204
    assert client.get(f"/api/cha/invoice-packing/{rec['id']}", headers=h).status_code == 404


def test_refuses_bad_lines_and_other_companies_cannot_see_it(h, world):
    bad = body("BAD"); bad["data"]["lines"][0]["qty"] = "-1"
    assert client.post("/api/cha/invoice-packing", json=bad, headers=h).status_code == 422
    assert client.post("/api/cha/invoice-packing", json={"invoice_no": "", "data": {}}, headers=h).status_code == 422
    rec = client.post("/api/cha/invoice-packing", json=body("PRIV"), headers=h).json()
    other = hdr(world["demo"], world["demo_license"])
    assert client.get(f"/api/cha/invoice-packing/{rec['id']}", headers=other).status_code in (403, 404)
    lst = client.get("/api/cha/invoice-packing", headers=other)
    assert lst.status_code == 403 or all(x["id"] != rec["id"] for x in lst.json()["items"])


def test_get_bill_fills_from_the_job(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="DXB(DUBAI)")
    client.put(f"/api/jobs/{j}/details/parties", json={"data": {"shipper": "PSR EXPORTERS", "consignee": "SYED"}}, headers=h)
    client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"cargoDesc": "ONION", "pkgs": "1650", "gwt": "1650", "nwt": "1650"}}, headers=h)
    r = client.get(f"/api/cha/invoice-packing/from-job/{j}", headers=h).json()
    d = r["data"]
    assert d["exporter"] == "PSR EXPORTERS" and d["consignee"] == "SYED" and d["port_of_loading"].startswith("MAA") and d["pre_carriage"] == "AIR"
    assert d["lines"][0]["description"] == "ONION" and d["lines"][0]["pkgs"] == "1650"
    assert client.get("/api/cha/invoice-packing/from-job/NOPE-1", headers=h).status_code == 404
