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


def test_automatic_invoice_numbers_run_in_order(h):
    n = client.get("/api/cha/invoice-packing/next-number", headers=h).json()["invoice_no"]
    assert n.startswith("CI/") and n.endswith("0001") or n[-4:].isdigit()
    a = client.post("/api/cha/invoice-packing", json={"invoice_date": "2026-11-10", "auto_number": True, "data": {"currency": "USD", "lines": []}}, headers=h).json()
    b = client.post("/api/cha/invoice-packing", json={"invoice_date": "2026-11-10", "auto_number": True, "data": {"currency": "USD", "lines": []}}, headers=h).json()
    assert a["invoice_no"].startswith("CI/26-27/") and int(b["invoice_no"][-4:]) == int(a["invoice_no"][-4:]) + 1
    man = client.post("/api/cha/invoice-packing", json={"invoice_no": "MY-001", "data": {"lines": []}}, headers=h).json()
    assert man["invoice_no"] == "MY-001"                                                  # manual numbers stay as typed
    assert client.put(f"/api/cha/invoice-packing/{a['id']}", json={"invoice_no": "", "data": {"lines": []}}, headers=h).status_code == 422


def test_refuses_bad_lines_and_other_companies_cannot_see_it(h, world):
    bad = body("BAD"); bad["data"]["lines"][0]["qty"] = "-1"
    assert client.post("/api/cha/invoice-packing", json=bad, headers=h).status_code == 422
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


def test_approve_revoke_and_cha_job_from_invoice(h):
    b = body("APR-1"); b["data"]["direction"] = "Export"; b["data"]["port_of_loading"] = "INMAA(Chennai)"; b["data"]["port_of_discharge"] = "USNYC(New York)"; b["data"]["pre_carriage"] = "SEA"
    b["data"]["lines"][0].update({"hs": "6109", "part_no": "PN-1", "gross_wt": "2.5", "qty_unit": "PCS"})
    rec = client.post("/api/cha/invoice-packing", json=b, headers=h).json()
    assert rec["approval"] == "DRAFT"
    assert client.get(f"/api/cha/invoice-packing/{rec['id']}/for-cha", headers=h).status_code == 409                      # not approved: cannot be fetched
    assert not client.get("/api/cha/invoice-packing?approved=true&q=APR-1", headers=h).json()["items"]
    ap = client.post(f"/api/cha/invoice-packing/{rec['id']}/approve", headers=h).json()
    assert ap["approval"] == "APPROVED" and ap["approved_by"]
    forged = body("APR-1"); forged["data"]["approval"] = {"status": "APPROVED"}
    assert client.post("/api/cha/invoice-packing", json=forged, headers=h).json()["approval"] == "DRAFT"                    # approval cannot be sent by the client
    f = client.get(f"/api/cha/invoice-packing/{rec['id']}/for-cha", headers=h).json()
    assert f["direction"] == "Export" and f["data"]["items"][0]["part_no"] == "PN-1" and f["data"]["items"][0]["ritc_no"] == "6109" and f["data"]["invoices"][0]["invoice_value"] == "430.00" and f["cargo"]["gwt"] == "2.5"
    j = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": f["exporter"], "importer": f["importer"], "pol": f["pol"], "pod": f["pod"], "invoice_id": rec["id"]}, headers=h)
    assert j.status_code == 201, j.text
    jn = j.json()["cha_job_number"]
    full = client.get(f"/api/cha/jobs/{jn}", headers=h).json()
    assert full["data"]["items"][0]["description"] == "GARMENTS" and full["data"]["invoices"][0]["invoice_no"] == "APR-1"
    assert client.get(f"/api/cha/invoice-packing/{rec['id']}", headers=h).json()["job_number"] == jn
    up = body("APR-1"); up["data"]["direction"] = "Export"; up["data"]["lines"][0]["qty"] = "9"
    assert client.put(f"/api/cha/invoice-packing/{rec['id']}", json=up, headers=h).json()["approval"] == "DRAFT"           # edited after approval: approve again
    client.post(f"/api/cha/invoice-packing/{rec['id']}/approve", headers=h)
    assert client.post(f"/api/cha/invoice-packing/{rec['id']}/revoke", headers=h).json()["approval"] == "DRAFT"
    assert client.post(f"/api/cha/invoice-packing/{rec['id']}/revoke", headers=h).status_code == 409
    imp = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Sea", "exporter": "X", "importer": "Y", "invoice_id": rec["id"], "allow_unapproved": True}, headers=h)
    assert imp.status_code == 409                                                                                           # an export invoice cannot create an import job


def test_dashboard_summary(h):
    r = client.get("/api/cha/invoice-packing/summary", headers=h).json()
    c = r["counts"]
    assert c["total"] == c["approved"] + c["draft"] == c["export"] + c["import"] and len(r["months"]) == 6 and isinstance(r["value"], list) and isinstance(r["top_parties"], list)
