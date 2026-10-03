import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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
def job(world, admin):
    h = hdr(admin["username"], admin["license"])
    made = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR"}, headers=h)
    assert made.status_code == 201, made.text
    return {"h": h, "no": made.json()["job_number"], "id": made.json()["id"]}


def line(name="Ocean Freight", qty="2", buy=None, sell=None):
    return {"charge_name": name, "description": "d", "uom": "Per Container", "qty": qty, "hsn": "996512",
            "buy": {"ccy": "USD", "exch": "83.1", "rate": "680", "tax_type": "Non-Taxable", "tax_pct": "0", "party": "MSC", "use_party": True, **(buy or {})},
            "sell": {"ccy": "USD", "exch": "83.5", "rate": "850", "tax_type": "Taxable", "tax_pct": "18", "party": "Client", "use_party": True, **(sell or {})}}


def test_server_calculates_amounts_and_totals_with_decimals(job):
    r = client.put(f"/api/shipments/by-number/{job['no']}/charges", json={"lines": [line()]}, headers=job["h"])
    assert r.status_code == 200, r.text
    row = r.json()["lines"][0]
    assert row["sell"]["amount"] == "1700.00" and row["sell"]["inr"] == "141950.00" and row["sell"]["tax"] == "25551.00"
    assert row["buy"]["inr"] == "113016.00" and row["buy"]["tax"] == "0.00"
    t = r.json()["totals"]
    assert t["ar"] == "141950.00" and t["ap"] == "113016.00" and t["gross_profit"] == "28934.00" and t["gp_percent"] == "20.38"
    assert t["ar_total"] == "167501.00" and t["ap_total"] == "113016.00"


def test_charges_persist_and_roundtrip(job):
    got = client.get(f"/api/shipments/by-number/{job['no']}/charges", headers=job["h"]).json()
    assert len(got["lines"]) == 1 and got["lines"][0]["charge_name"] == "Ocean Freight" and got["totals"]["gross_profit"] == "28934.00"
    shipments = client.get("/api/shipments", headers=job["h"]).json()
    assert Decimal(str([s for s in shipments if s["job_number"] == job["no"]][0]["revenue"])) == Decimal("141950.00")


def test_half_up_rounding_and_inr_ignores_exchange_rate(job):
    r = client.put(f"/api/shipments/by-number/{job['no']}/charges", json={"lines": [line(qty="1", buy={"ccy": "INR", "exch": "99", "rate": "0.005"}, sell={"ccy": "INR", "exch": "99", "rate": "10.005", "tax_type": "Non-Taxable"})]}, headers=job["h"])
    row = r.json()["lines"][0]
    assert row["buy"]["inr"] == "0.01" and row["sell"]["inr"] == "10.01" and row["buy"]["exch"] == "1.000000"


def test_replace_all_and_clear(job):
    client.put(f"/api/shipments/by-number/{job['no']}/charges", json={"lines": [line(), line("THC")]}, headers=job["h"])
    two = client.get(f"/api/shipments/by-number/{job['no']}/charges", headers=job["h"]).json()
    assert [l["line_no"] for l in two["lines"]] == [1, 2]
    cleared = client.put(f"/api/shipments/by-number/{job['no']}/charges", json={"lines": []}, headers=job["h"]).json()
    assert cleared["lines"] == [] and cleared["totals"]["ar"] == "0"


def test_validation(job):
    url = f"/api/shipments/by-number/{job['no']}/charges"
    assert client.put(url, json={"lines": [line(buy={"exch": "0"})]}, headers=job["h"]).status_code == 422
    assert client.put(url, json={"lines": [line(sell={"rate": "-1"})]}, headers=job["h"]).status_code == 422
    assert client.put(url, json={"lines": [line(sell={"ccy": "usd"})]}, headers=job["h"]).status_code == 422
    assert client.put(url, json={"lines": [line(sell={"tax_pct": "101"})]}, headers=job["h"]).status_code == 422
    assert client.put(url, json={"lines": [line(qty="-1")]}, headers=job["h"]).status_code == 422
    assert client.put(url, json={"lines": [line()] * 201}, headers=job["h"]).status_code == 422


def test_tenant_isolation_and_permissions(world, admin, job):
    url = f"/api/shipments/by-number/{job['no']}/charges"
    other = hdr(world["demo"], portal="demo")
    assert client.get(url, headers=other).status_code in (403, 404)
    assert client.put(url, json={"lines": [line()]}, headers=other).status_code in (403, 404)
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER",):
        client.post("/api/users", json={"username": f"v{tag}", "email": f"v{tag}@c.test", "password": PASSWORD, "first_name": "V", "role": role}, headers=job["h"])
    viewer = hdr(f"v{tag}", admin["license"])
    assert client.get(url, headers=viewer).status_code == 200
    assert client.put(url, json={"lines": [line()]}, headers=viewer).status_code == 403
    assert client.get(url).status_code == 401
    dev = hdr(world["dev"], portal="developer")
    assert client.get(url, headers=dev).status_code == 403
    assert client.get("/api/shipments/by-number/NOPE/charges", headers=job["h"]).status_code == 404
