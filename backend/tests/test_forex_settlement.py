"""Forex: an overseas invoice in USD at 83, settled by receipts at other rates: realised gain / loss, partial and multiple settlements, balances, journals."""
from decimal import Decimal

import pytest

from tests.test_billing import client
from tests.test_credit_notes_ledger import balanced, doc, party
from tests.test_gl import D, h  # noqa: F401


def usd_invoice(h, name, usd="1000", rate="83"):
    d = doc(h, "invoice", name, usd, kind="Non-Taxable", ccy="USD", exch=rate)
    assert D(d["grand_total"]) == D(usd) * D(rate)
    return d


def receipt(h, name, doc_id, fx_amount, fx_rate, amount=None, **extra):
    cash = amount if amount is not None else str(D(fx_amount) * D(fx_rate))
    return client.post("/api/payments", json={"direction": "RECEIPT", "party_name": name, "method": "Bank Transfer", "reference": "SWIFT1", "amount": cash,
                                              "allocations": [{"document_id": doc_id, "amount": "1", "fx_amount": fx_amount, "fx_rate": fx_rate, **extra}]}, headers=h)


def entries(h, number):
    full = balanced(h, number)
    return {e["ledger_name"] if "ledger_name" in e else e.get("ledger"): (D(e["debit"]), D(e["credit"])) for e in full["entries"]}


def test_full_settlement_at_a_higher_rate_is_a_realised_gain(h):
    name = party(h)
    inv = usd_invoice(h, name)
    fx = client.get(f"/api/payments/fx/{inv['id']}", headers=h).json()["fx"]
    assert fx["ccy"] == "USD" and fx["fc_balance"] == "1000.00" and D(fx["rate"]) == 83
    r = receipt(h, name, inv["id"], "1000", "84")
    assert r.status_code == 201, r.text
    p = r.json()
    a = p["allocations"][0]
    assert D(p["amount"]) == 84000 and D(a["amount"]) == 83000 and D(a["fx_gain"]) == 1000 and D(a["fx_cash"]) == 84000
    now = client.get(f"/api/documents/{inv['id']}", headers=h).json()
    assert now["payment_status"] == "PAID" and D(now["outstanding"]) == 0                       # the invoice is cleared at its book value
    e = entries(h, p["pay_number"])
    assert any(D(dr) == 84000 for dr, cr in e.values())                                          # Dr Bank 84,000
    assert any(cr == 83000 for dr, cr in e.values()) and any(cr == 1000 for dr, cr in e.values())      # Cr customer 83,000, Cr forex gain 1,000
    assert any("Realised Forex Gain" in str(k) for k in e)
    assert client.get(f"/api/payments/fx/{inv['id']}", headers=h).json()["fx"]["fc_balance"] == "0.00"


def test_loss_partial_and_multiple_settlements(h):
    name = party(h)
    inv = usd_invoice(h, name)
    r1 = receipt(h, name, inv["id"], "400", "82")                                               # 400 x 82 = 32,800 against 33,200: loss 400
    assert r1.status_code == 201, r1.text
    a1 = r1.json()["allocations"][0]
    assert D(a1["amount"]) == 33200 and D(a1["fx_gain"]) == -400
    mid = client.get(f"/api/documents/{inv['id']}", headers=h).json()
    assert D(mid["outstanding"]) == 49800 and mid["payment_status"] == "PARTIAL"
    assert client.get(f"/api/payments/fx/{inv['id']}", headers=h).json()["fx"]["fc_balance"] == "600.00"
    e1 = entries(h, r1.json()["pay_number"])
    assert any("Realised Forex Loss" in str(k) for k in e1)
    r2 = receipt(h, name, inv["id"], "600", "85")                                               # last 600 x 85 = 51,000 against 49,800: gain 1,200
    assert r2.status_code == 201, r2.text
    a2 = r2.json()["allocations"][0]
    assert D(a2["amount"]) == 49800 and D(a2["fx_gain"]) == 1200
    end = client.get(f"/api/documents/{inv['id']}", headers=h).json()
    assert end["payment_status"] == "PAID" and D(end["outstanding"]) == 0
    over = receipt(h, name, inv["id"], "1", "84")
    assert over.status_code == 422                                                              # nothing left to settle


def test_rupee_documents_and_rules_are_unchanged(h):
    name = party(h)
    inr = doc(h, "invoice", name, "1000")
    assert client.get(f"/api/payments/fx/{inr['id']}", headers=h).json()["fx"] is None
    bad = receipt(h, name, inr["id"], "10", "84")
    assert bad.status_code == 422                                                               # a rupee invoice is settled in rupees
    ok = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": name, "method": "Cash", "amount": "1180", "allocations": [{"document_id": inr["id"], "amount": "1180"}]}, headers=h)
    assert ok.status_code == 201 and ok.json()["allocations"][0]["fx_gain"] == "0"
    half = receipt(h, name, usd_invoice(h, name)["id"], "100", "83")
    assert half.status_code == 201 and D(half.json()["allocations"][0]["fx_gain"]) == 0         # same rate: no difference
