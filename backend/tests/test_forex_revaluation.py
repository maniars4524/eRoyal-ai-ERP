"""Forex revaluation of open foreign currency invoices / bills: unrealised gain / loss journal, automatic reversal, no duplicates, later settlement, trial balance."""
from datetime import date, timedelta
from decimal import Decimal

from tests.test_billing import client
from tests.test_credit_notes_ledger import doc, party
from tests.test_gl import D, h, tb  # noqa: F401


def bal(h, name, **kw):
    """Closing balance (debit positive) of a ledger in the trial balance."""
    rows = [r for r in tb(h, **kw)["rows"] if r.get("kind") == "ledger" and r.get("name") == name]
    return D(rows[0]["closing"]) if rows else D(0)


def mv(h, name, **kw):
    rows = [r for r in tb(h, **kw)["rows"] if r.get("kind") == "ledger" and r.get("name") == name]
    return (D(rows[0]["debit"]) - D(rows[0]["credit"])) if rows else D(0)


def test_revaluation_gain_reversal_duplicates_and_settlement(h):
    name = party(h)
    inv = doc(h, "invoice", name, "1000", kind="Non-Taxable", ccy="USD", exch="83")
    today = date.today()
    nxt, later = today + timedelta(days=1), today + timedelta(days=3)
    gain0, loss0 = mv(h, "Unrealised Forex Gain"), mv(h, "Unrealised Forex Loss")
    openl = client.get(f"/api/forex/open?as_of={today}", headers=h).json()
    mine = [i for i in openl["items"] if i["document_id"] == inv["id"]]
    assert "USD" in openl["currencies"] and mine and mine[0]["fc_balance"] == "1000.00" and D(mine[0]["carrying"]) == 83000
    assert client.post("/api/forex/revalue", json={"as_of": str(today), "rates": {}}, headers=h).status_code == 422       # the closing rate is needed
    r = client.post("/api/forex/revalue", json={"as_of": str(today), "rates": {"USD": "85"}}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["revalued"] >= 1 and r.json()["voucher"].startswith("JV/") and r.json()["reversal_date"] == str(nxt)
    party_name = name
    # on the reporting date: the debtor carries 85,000 and the unrealised gain is 2,000 (this invoice's share is in the journal)
    assert mv(h, "Unrealised Forex Gain", date_to=today) - gain0 == D("-2000")                     # credit side: negative debit-minus-credit
    pl = next(x for x in client.get("/api/coa/ledgers", headers=h).json() if x["name"] == party_name)
    led = client.get(f"/api/gl/ledger?ledger_id={pl['id']}&date_to={today}", headers=h).json()
    assert D(led["closing"] if "closing" in led else led["totals"]["closing"]) == D("85000")
    # the next day the revaluation is reversed automatically
    assert mv(h, "Unrealised Forex Gain", date_to=nxt) - gain0 == D(0)
    assert tb(h, date_to=today)["balanced"] and tb(h, date_to=nxt)["balanced"]
    dup = client.post("/api/forex/revalue", json={"as_of": str(today), "rates": {"USD": "86"}}, headers=h)
    assert dup.status_code == 409                                                                    # never revalued twice for the same date
    hist = client.get("/api/forex/revaluations", headers=h).json()
    row = next(x for x in hist if x["document"] == inv["doc_number"])
    assert D(row["carrying_inr"]) == 83000 and D(row["revalued_inr"]) == 85000 and D(row["unrealised"]) == 2000 and row["status"] == "ACTIVE"
    # settlement afterwards: realised gain against the ORIGINAL invoice value (the reversal has already cleaned the books)
    rc = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": name, "method": "Bank Transfer", "reference": "SW1", "pay_date": str(later), "amount": "86000",
                                            "allocations": [{"document_id": inv["id"], "amount": "1", "fx_amount": "1000", "fx_rate": "86"}]}, headers=h)
    assert rc.status_code == 201, rc.text
    assert D(rc.json()["allocations"][0]["fx_gain"]) == 3000
    led2 = client.get(f"/api/gl/ledger?ledger_id={pl['id']}", headers=h).json()
    assert D(led2["closing"] if "closing" in led2 else led2["totals"]["closing"]) == 0              # the customer account is clear
    assert tb(h)["balanced"]
    # the revaluation can be undone and made again with another rate (journal and reversal removed)
    assert client.delete(f"/api/forex/revaluations/{today}", headers=h).status_code == 200
    assert mv(h, "Unrealised Forex Gain", date_to=today) - gain0 == D(0)


def test_payable_revaluation_is_an_unrealised_loss_when_the_rate_rises(h):
    name = party(h, "vendor")
    pi = doc(h, "purchase_invoice", name, "500", kind="Non-Taxable", ccy="USD", exch="80")
    today = date.today()
    loss0 = mv(h, "Unrealised Forex Loss")
    r = client.post("/api/forex/revalue", json={"as_of": str(today), "rates": {"USD": "85"}}, headers=h)
    assert r.status_code == 201, r.text
    row = next(x for x in client.get("/api/forex/revaluations", headers=h).json() if x["document"] == pi["doc_number"])
    assert D(row["carrying_inr"]) == 40000 and D(row["revalued_inr"]) == 42500 and D(row["unrealised"]) == -2500       # liability up: loss
    assert mv(h, "Unrealised Forex Loss", date_to=today) - loss0 == D("2500")
    assert tb(h, date_to=today)["balanced"]
    client.delete(f"/api/forex/revaluations/{today}", headers=h)


def test_forex_reports(h):
    name = party(h)
    inv = doc(h, "invoice", name, "1000", kind="Non-Taxable", ccy="USD", exch="83")
    rc = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": name, "method": "Bank Transfer", "reference": "SW9", "amount": "33600",
                                            "allocations": [{"document_id": inv["id"], "amount": "1", "fx_amount": "400", "fx_rate": "84"}]}, headers=h)
    assert rc.status_code == 201, rc.text
    today = date.today()
    real = client.get("/api/forex/reports/realised", headers=h).json()
    row = next(r for r in real["rows"] if r["document"] == inv["doc_number"])
    assert row["invoice_rate"] == "83.0000" and row["settlement_rate"].startswith("84") and D(row["book_inr"]) == 33200 and D(row["cash_inr"]) == 33600 and D(row["gain_loss"]) == 400
    assert D(real["net"]) >= 400
    out = client.get("/api/forex/reports/outstanding", headers=h).json()["rows"]
    mine = next(r for r in out if r["document"] == inv["doc_number"])
    assert mine["kind"] == "Receivable" and mine["fc_balance"] == "600.00" and D(mine["carrying_inr"]) == 49800
    inw = next(r for r in client.get("/api/forex/reports/invoicewise", headers=h).json()["rows"] if r["document"] == inv["doc_number"])
    assert D(inw["realised"]) == 400 and inw["fc_settled"] == "400.00" and inw["fc_balance"] == "600.00"
    rec = next(r for r in client.get("/api/forex/reports/reconciliation", headers=h).json()["rows"] if r["party"] == name)
    assert rec["invoiced"] == "1000.00" and rec["settled"] == "400.00" and rec["balance"] == "600.00" and D(rec["book_inr"]) == 49800 and rec["reconciled"] is True
    r = client.post("/api/forex/revalue", json={"as_of": str(today), "rates": {"USD": "85"}}, headers=h)
    assert r.status_code in (201, 409), r.text
    un = client.get("/api/forex/reports/unrealised", headers=h).json()
    assert any(x["document"] == inv["doc_number"] and D(x["gain_loss"]) == 1200 for x in un["rows"]) or r.status_code == 409          # 600 x 85 = 51,000 against 49,800
    client.delete(f"/api/forex/revaluations/{today}", headers=h)


def test_forex_shows_in_profit_loss_balance_sheet_ledger_and_day_book(h):
    name = party(h)
    inv = doc(h, "invoice", name, "1000", kind="Non-Taxable", ccy="USD", exch="83")
    rc = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": name, "method": "Bank Transfer", "reference": "SW7", "amount": "84000",
                                            "allocations": [{"document_id": inv["id"], "amount": "1", "fx_amount": "1000", "fx_rate": "84"}]}, headers=h)
    assert rc.status_code == 201, rc.text
    pl = client.get("/api/gl/profit-loss", headers=h)
    assert pl.status_code == 200 and "Realised Forex Gain" in pl.text                                   # income side of the profit and loss
    bs = client.get("/api/gl/balance-sheet", headers=h)
    assert bs.status_code == 200 and bs.json() is not None
    led = next(x for x in client.get("/api/coa/ledgers", headers=h).json() if x["name"] == "Realised Forex Gain")
    rep = client.get(f"/api/gl/ledger?ledger_id={led['id']}", headers=h).json()
    assert any(D(r["credit"]) == 1000 and rc.json()["pay_number"] in str(r) for r in rep["entries"])      # its ledger shows the credit against the receipt voucher
    assert any(v["number"] == rc.json()["pay_number"] for v in client.get("/api/gl/day-book", headers=h).json()["vouchers"])
    assert tb(h)["balanced"]
