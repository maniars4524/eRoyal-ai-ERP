import uuid

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
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def allcur(h):
    return client.get("/api/masters/currency", params={"limit": 1000}, headers=h).json()


def cur(h, code):
    return next((r for r in allcur(h) if r["code"] == code), None)


def test_seed_is_idempotent_complete_and_uses_iso_values(h):
    r = client.post("/api/masters/currency/seed", headers=h)
    assert r.status_code == 201, r.text
    first = r.json()
    assert set(first["created"]) | set(first["skipped"]) == {"INR", "USD", "EUR", "GBP", "AED", "SAR", "SGD", "AUD", "CAD", "JPY", "BDT", "LKR", "CNY", "CHF", "MYR"}
    again = client.post("/api/masters/currency/seed", headers=h).json()
    assert again["created"] == [] and len(again["skipped"]) == 15
    assert len([r for r in allcur(h) if r["code"] in ("USD", "INR", "AUD")]) == 3
    usd, aud, bdt, jpy, inr, aed = (cur(h, c)["data"] for c in ("USD", "AUD", "BDT", "JPY", "INR", "AED"))
    assert usd["currency_symbol"] == "$" and usd["unicode_symbol"] == "U+0024" and usd["iso_numeric_code"] == "840" and usd["decimal_places"] == "2"
    assert usd["sub_currency_name"] == "Cent" and usd["sub_currency_ratio"] == "100" and usd["symbol_position"] == "PREFIX"
    assert aud["iso_numeric_code"] == "036" and bdt["iso_numeric_code"] == "050"  # leading zeros are kept (text, not numbers)
    assert jpy["decimal_places"] == "0" and aed["unicode_symbol"] == "U+062F U+002E U+0625" and inr["unicode_symbol"] == "U+20B9"
    assert all(cur(h, c)["data"]["is_system_currency"] == "yes" for c in ("USD", "INR"))
    assert usd["default_currency"] == "No"


def test_field_validation_on_the_server(h):
    ok = {"code": "NOK", "name": "Norwegian Krone", "data": {"currency_symbol": "kr", "decimal_places": "2"}}
    assert client.post("/api/masters/currency", json={**ok, "code": "ZZZ"}, headers=h).status_code == 422  # not an ISO 4217 code
    bad_dec = client.post("/api/masters/currency", json={**ok, "data": {"decimal_places": "3"}}, headers=h)
    assert bad_dec.status_code == 422 and "2 decimal places" in bad_dec.json()["detail"]
    assert client.post("/api/masters/currency", json={**ok, "data": {"decimal_places": "-1"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "data": {"iso_numeric_code": "999"}}, headers=h).status_code == 422  # wrong numeric for NOK (578)
    assert client.post("/api/masters/currency", json={**ok, "data": {"iso_numeric_code": "57"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "data": {"sub_currency_ratio": "0"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "data": {"sub_currency_ratio": "abc"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "data": {"symbol_position": "MIDDLE"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "data": {"unicode_symbol": "kr"}}, headers=h).status_code == 422
    assert client.post("/api/masters/currency", json={**ok, "name": "  "}, headers=h).status_code == 422
    created = client.post("/api/masters/currency", json={**ok, "code": "nok", "data": {"currency_symbol": "kr", "symbol_position": "Suffix (100 kr)", "sub_currency_ratio": "100", "unicode_symbol": "u+006b u+0072"}}, headers=h)
    assert created.status_code == 201, created.text
    d = created.json()["data"]
    assert created.json()["code"] == "NOK" and d["iso_numeric_code"] == "578" and d["decimal_places"] == "2" and d["symbol_position"] == "SUFFIX"
    assert d["unicode_symbol"] == "U+006B U+0072" and d["default_currency"] == "No"
    assert client.post("/api/masters/currency", json=ok, headers=h).status_code == 409  # unique per company


def test_update_keeps_unrelated_data_and_protects_system_flags(h):
    client.post("/api/masters/currency/seed", headers=h)
    eur = cur(h, "EUR")
    up = client.patch(f"/api/masters/currency/{eur['id']}", json={"data": {**eur["data"], "notes": "Euro zone", "exchange_rate": "90.5", "is_system_currency": "no", "system_default": "Yes"}}, headers=h)
    assert up.status_code == 200, up.text
    d = up.json()["data"]
    assert d["notes"] == "Euro zone" and d["exchange_rate"] == "90.5" and d["currency_symbol"] == "€" and d["sub_currency_name"] == "Cent"
    assert d["is_system_currency"] == "yes" and d["system_default"] == eur["data"]["system_default"]  # system-managed flags cannot be edited by a client
    assert client.patch(f"/api/masters/currency/{eur['id']}", json={"code": "XYZ"}, headers=h).status_code in (409, 422)  # a system currency code is fixed


def test_deletion_rules(h):
    client.post("/api/masters/currency/seed", headers=h)
    usd = cur(h, "USD")
    r = client.delete(f"/api/masters/currency/{usd['id']}", headers=h)
    assert r.status_code == 409 and "system currency" in r.json()["detail"]
    mine = client.post("/api/masters/currency", json={"code": "SEK", "name": "Swedish Krona", "data": {}}, headers=h).json()
    q = client.post("/api/quotations", json={"party_name": "Acme", "mode": "Sea", "direction": "Export", "lines": [{"side": "SELL", "charge_name": "Freight", "basis": "Shipment", "ccy": "SEK", "exch": "8", "rate": "100", "qty": "1"}]}, headers=h)
    assert q.status_code == 201, q.text
    used = client.delete(f"/api/masters/currency/{mine['id']}", headers=h)
    assert used.status_code == 409 and "quotations" in used.json()["detail"] and "Deactivate" in used.json()["detail"]
    assert client.patch(f"/api/masters/currency/{mine['id']}", json={"status": "INACTIVE"}, headers=h).json()["status"] == "INACTIVE"
    assert cur(h, "SEK")["status"] == "INACTIVE"  # inactive currencies stay available for historical reports
    free = client.post("/api/masters/currency", json={"code": "DKK", "name": "Danish Krone", "data": {}}, headers=h).json()
    assert client.delete(f"/api/masters/currency/{free['id']}", headers=h).status_code == 204


def test_base_currency_rules(h):
    client.post("/api/masters/currency/seed", headers=h)
    usd = cur(h, "USD")
    bases = [r for r in allcur(h) if r["data"].get("default_currency") == "Yes"]
    if not bases:  # a company outside India has no base yet: the first one is chosen explicitly
        assert client.post(f"/api/masters/currency/{cur(h, 'INR')['id']}/set-base", json={"confirm": True, "reason": "Initial base currency"}, headers=h).status_code == 200
        bases = [r for r in allcur(h) if r["data"].get("default_currency") == "Yes"]
    base_now = bases[0]
    other = usd if usd["id"] != base_now["id"] else cur(h, "EUR")
    # the base currency cannot be changed through the ordinary form
    assert client.patch(f"/api/masters/currency/{other['id']}", json={"data": {**other["data"], "default_currency": "Yes"}}, headers=h).status_code == 409
    assert client.patch(f"/api/masters/currency/{base_now['id']}", json={"data": {**base_now["data"], "default_currency": "No"}}, headers=h).status_code == 409
    assert client.patch(f"/api/masters/currency/{base_now['id']}", json={"status": "INACTIVE"}, headers=h).status_code == 409
    assert client.delete(f"/api/masters/currency/{base_now['id']}", headers=h).status_code == 409
    assert client.post("/api/masters/currency", json={"code": "NZD", "name": "New Zealand Dollar", "data": {"default_currency": "Yes"}}, headers=h).status_code == 409
    # the explicit action needs confirmation and a reason
    assert client.post(f"/api/masters/currency/{other['id']}/set-base", json={"confirm": True}, headers=h).status_code == 422
    assert client.post(f"/api/masters/currency/{other['id']}/set-base", json={"reason": "Group policy change"}, headers=h).status_code == 422
    assert client.post(f"/api/masters/currency/{base_now['id']}/set-base", json={"confirm": True, "reason": "Same currency again"}, headers=h).status_code == 409
    moved = client.post(f"/api/masters/currency/{other['id']}/set-base", json={"confirm": True, "reason": "Group reporting currency changed"}, headers=h)
    assert moved.status_code == 200 and moved.json()["data"]["default_currency"] == "Yes"
    assert [r["code"] for r in allcur(h) if r["data"].get("default_currency") == "Yes"] == [other["code"]]  # exactly one base currency
    assert client.post(f"/api/masters/currency/{base_now['id']}/set-base", json={"confirm": True, "reason": "Restore the home currency"}, headers=h).status_code == 200


def test_posted_invoices_freeze_the_base_currency_and_permissions(h, admin):
    name = "cv" + uuid.uuid4().hex[:6]
    assert client.post("/api/users", json={"username": name, "email": name + "@t.test", "password": PASSWORD, "first_name": "V", "role": "OPERATIONS"}, headers=h).status_code in (200, 201)
    ops = hdr(name, admin["license"])
    eur = cur(h, "EUR")
    assert client.post(f"/api/masters/currency/{eur['id']}/set-base", json={"confirm": True, "reason": "Operations should not do this"}, headers=ops).status_code == 403
    assert client.post("/api/masters/currency/seed", headers=ops).status_code == 403
    ref = client.get("/api/masters/currency/iso-reference", headers=ops)
    assert ref.status_code == 200 and any(x["code"] == "AUD" and x["numeric"] == "036" for x in ref.json())
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Freeze Co", "lines": [{"charge_name": "x", "rate": "100"}]}, headers=h).json()
    assert client.post(f"/api/documents/{d['id']}/post", headers=h).status_code == 200
    frozen = client.post(f"/api/masters/currency/{eur['id']}/set-base", json={"confirm": True, "reason": "Too late to change this"}, headers=h)
    assert frozen.status_code == 409 and "Posted invoices exist" in frozen.json()["detail"]
