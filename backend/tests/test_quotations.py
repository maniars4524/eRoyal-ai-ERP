import threading
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.billing import fiscal_year
from app.db import SessionLocal
from app.main import app
from app.models import Quotation

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


def line(name="Ocean Freight", carrier="", rate="850", ccy="USD", exch="83.5", qty="2", basis="Per Container", tax="0"):
    return {"charge_name": name, "carrier": carrier, "basis": basis, "ccy": ccy, "exch": exch, "rate": rate, "qty": qty, "tax_pct": tax}


def draft(h, **extra):
    body = {"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "DEHAM",
            "lines": [line(), line("THC", rate="6500", ccy="INR", exch="1", qty="2", basis="Per Container")], **extra}
    r = client.post("/api/quotations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_draft_amounts_are_calculated_on_the_server(h):
    q = draft(h)
    assert q["status"] == "DRAFT" and q["number"] is None
    assert [l["amount"] for l in q["lines"]] == ["141950.00", "13000.00"] and q["quoted_amount"] == "154950.00"
    assert q["valid_until"] == str(date.today() + timedelta(days=15))


def test_multi_carrier_totals_and_quoted_amount_is_the_lowest(h):
    q = draft(h, carrier_mode="multi", lines=[line(carrier="MSC", rate="900"), line("THC", carrier="MSC", rate="6000", ccy="INR", exch="1"), line(carrier="ONE", rate="800")])
    assert q["carrier_totals"]["ONE"] == "133600.00" and q["carrier_totals"]["MSC"] == "162300.00"
    assert q["quoted_amount"] == "133600.00"


def test_editing_a_draft_replaces_lines(h):
    q = draft(h)
    r = client.patch(f"/api/quotations/{q['id']}", json={"party_name": "Acme Ltd", "pod": "BEANR", "lines": [line("Only", rate="100", ccy="INR", exch="1", qty="3")]}, headers=h)
    assert r.status_code == 200, r.text
    assert [l["charge_name"] for l in r.json()["lines"]] == ["Only"] and r.json()["quoted_amount"] == "300.00" and r.json()["pod"] == "BEANR"
    assert client.patch(f"/api/quotations/{q['id']}", json={"lines": [line("a"), line("b"), line("c")]}, headers=h).status_code == 200


def test_send_numbers_gap_free_and_locks(h):
    nums = []
    for _ in range(3):
        q = draft(h)
        sent = client.post(f"/api/quotations/{q['id']}/send", headers=h)
        assert sent.status_code == 200, sent.text
        nums.append(sent.json()["number"])
    fy = fiscal_year(date.today())
    seq = [int(n.rsplit("/", 1)[1]) for n in nums]
    assert all(n.startswith(f"QT/{fy}/") for n in nums) and seq == list(range(seq[0], seq[0] + 3))
    assert client.patch(f"/api/quotations/{q['id']}", json={"party_name": "x"}, headers=h).status_code == 409
    assert client.delete(f"/api/quotations/{q['id']}", headers=h).status_code == 409
    assert client.post(f"/api/quotations/{q['id']}/send", headers=h).status_code == 409


def test_concurrent_sends_never_duplicate_numbers(h):
    ids = [draft(h)["id"] for _ in range(6)]
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(TestClient(app).post(f"/api/quotations/{i}/send", headers=h).json()["number"])) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(out) == 6 and len(set(out)) == 6


def test_send_validation(h):
    blank = client.post("/api/quotations", json={"party_name": ""}, headers=h).json()
    assert client.post(f"/api/quotations/{blank['id']}/send", headers=h).status_code == 422
    nolines = client.post("/api/quotations", json={"party_name": "X"}, headers=h).json()
    assert client.post(f"/api/quotations/{nolines['id']}/send", headers=h).status_code == 422
    zero = client.post("/api/quotations", json={"party_name": "X", "lines": [line(rate="0")]}, headers=h).json()
    assert client.post(f"/api/quotations/{zero['id']}/send", headers=h).status_code == 422
    past = client.post("/api/quotations", json={"party_name": "X", "valid_until": str(date.today() - timedelta(days=1)), "lines": [line()]}, headers=h).json()
    assert client.post(f"/api/quotations/{past['id']}/send", headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "mode": "Teleport"}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "lines": [line(ccy="USD", exch="0")]}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "customer_id": 99999999}, headers=h).status_code == 404


def test_draft_can_be_deleted(h):
    q = draft(h)
    assert client.delete(f"/api/quotations/{q['id']}", headers=h).status_code == 204
    assert client.get(f"/api/quotations/{q['id']}", headers=h).status_code == 404


def test_accept_reject_and_expiry(h):
    q = draft(h)
    assert client.post(f"/api/quotations/{q['id']}/accept", headers=h).status_code == 409  # not sent yet
    sent = client.post(f"/api/quotations/{q['id']}/send", headers=h).json()
    rej = client.post(f"/api/quotations/{sent['id']}/reject", headers=h).json()
    assert rej["status"] == "REJECTED" and client.post(f"/api/quotations/{sent['id']}/accept", headers=h).status_code == 409
    old = client.post(f"/api/quotations/{draft(h)['id']}/send", headers=h).json()
    db = SessionLocal()
    row = db.get(Quotation, old["id"])
    row.valid_until = date.today() - timedelta(days=2)
    db.commit()
    db.close()
    got = client.get(f"/api/quotations/{old['id']}", headers=h).json()
    assert got["status"] == "SENT" and got["effective_status"] == "EXPIRED"
    assert client.post(f"/api/quotations/{old['id']}/accept", headers=h).status_code == 409
    assert old["id"] in [x["id"] for x in client.get("/api/quotations?status=EXPIRED", headers=h).json()]


def test_convert_creates_a_job_with_the_quoted_charges(h):
    q = draft(h)
    assert client.post(f"/api/quotations/{q['id']}/convert", headers=h).status_code == 409
    client.post(f"/api/quotations/{q['id']}/send", headers=h)
    client.post(f"/api/quotations/{q['id']}/accept", headers=h)
    done = client.post(f"/api/quotations/{q['id']}/convert", headers=h)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "CONVERTED" and body["job_number"]
    charges = client.get(f"/api/shipments/by-number/{body['job_number']}/charges", headers=h).json()
    assert len(charges["lines"]) == 2 and charges["totals"]["ar"] == "154950.00" and charges["lines"][0]["sell"]["party"] == "Acme Exports"
    job = next(j for j in client.get("/api/shipments", headers=h).json() if j["job_number"] == body["job_number"])
    assert job["pol"] == "INMAA" and job["pod"] == "DEHAM" and job["container_mode"] == "FCL" and float(job["revenue"]) == 154950.0
    assert client.post(f"/api/quotations/{q['id']}/convert", headers=h).status_code == 409


def test_convert_multi_carrier_requires_a_choice(h):
    q = draft(h, carrier_mode="multi", lines=[line(carrier="MSC", rate="900"), line(carrier="ONE", rate="800")])
    client.post(f"/api/quotations/{q['id']}/send", headers=h)
    client.post(f"/api/quotations/{q['id']}/accept", headers=h)
    assert client.post(f"/api/quotations/{q['id']}/convert", headers=h).status_code == 422
    assert client.post(f"/api/quotations/{q['id']}/convert?carrier=Nobody", headers=h).status_code == 422
    ok = client.post(f"/api/quotations/{q['id']}/convert?carrier=ONE", headers=h)
    assert ok.status_code == 200
    charges = client.get(f"/api/shipments/by-number/{ok.json()['job_number']}/charges", headers=h).json()
    assert len(charges["lines"]) == 1 and charges["lines"][0]["sell"]["rate"].startswith("800")


def test_tenant_isolation_and_permissions(world, admin, h):
    q = draft(h)
    demo = hdr(world["demo"], portal="demo")
    assert client.get(f"/api/quotations/{q['id']}", headers=demo).status_code in (403, 404)
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER", "ACCOUNTS"):
        client.post("/api/users", json={"username": f"{role[:2].lower()}{tag}", "email": f"{role[:2].lower()}{tag}@q.test", "password": PASSWORD, "first_name": role, "role": role}, headers=h)
    viewer, acct = hdr(f"vi{tag}", admin["license"]), hdr(f"ac{tag}", admin["license"])
    assert client.get(f"/api/quotations/{q['id']}", headers=viewer).status_code == 200
    assert client.post("/api/quotations", json={"party_name": "x"}, headers=viewer).status_code == 403
    assert client.post("/api/quotations", json={"party_name": "x"}, headers=acct).status_code == 403
    assert client.get("/api/quotations").status_code == 401
    assert client.get("/api/quotations", headers=hdr(world["dev"], portal="developer")).status_code == 403
