"""Place of supply Outside India: GST is nil (export under LUT) unless the user chooses to charge it as per the tax % of each line."""
import pytest

from tests.test_billing import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def line():
    return {"charge_name": "Detention Charges", "description": "YHVU0987654- ( 12 DAYS )", "hsn": "996799", "uom": "Per Shipment", "qty": "1", "ccy": "INR", "exch": "1", "rate": "1000", "tax_type": "Taxable", "tax_pct": "18"}


def mk(h, header=None):
    body = {"doc_type": "invoice", "party_name": "OVERSEAS BUYER BV", "supply_type": "inter", "place_of_supply": "Outside India", "lines": [line()]}
    if header is not None:
        body["header"] = header
    r = client.post("/api/documents", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_export_gst_is_nil_by_default_and_can_be_charged(h):
    d = mk(h)
    assert float(d["tax_total"]) == 0 and float(d["grand_total"]) == 1000                      # export under LUT: nil
    assert d["lines"][0]["description"] == "YHVU0987654- ( 12 DAYS )"                          # the description of the cost entry is kept
    d2 = mk(h, {"charge_gst": True})
    assert float(d2["tax_total"]) == 180 and float(d2["grand_total"]) == 1180                  # charged as per the line's tax %
    r = client.patch(f"/api/documents/{d['id']}", json={"header": {"charge_gst": True}}, headers=h)
    assert r.status_code == 200 and float(r.json()["tax_total"]) == 180                        # switching it on later works the totals again
    r = client.patch(f"/api/documents/{d['id']}", json={"header": {"charge_gst": False}}, headers=h)
    assert r.status_code == 200 and float(r.json()["tax_total"]) == 0
