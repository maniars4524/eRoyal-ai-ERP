import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"
D = Decimal


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def ledger_of(h, name):
    return next(l for l in client.get("/api/coa/ledgers", headers=h).json() if l["name"] == name)


def lrep(h, name, **kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    return client.get(f"/api/gl/ledger?ledger_id={ledger_of(h, name)['id']}&{q}", headers=h).json()


def invoice(h, party, rate, intra=False, tax="18", kind="Taxable", charge="Ocean Freight", doc_type="invoice", ref=None):
    body = {"doc_type": doc_type, "party_name": party, "supply_type": "intra" if intra else "inter", "lines": [{"charge_name": charge, "qty": "1", "rate": rate, "tax_type": kind, "tax_pct": tax if kind == "Taxable" else "0"}]}
    if ref:
        body["ref_document_id"] = ref
    d = client.post("/api/documents", json=body, headers=h)
    assert d.status_code == 201, d.text
    p = client.post(f"/api/documents/{d.json()['id']}/post", headers=h)
    assert p.status_code == 200, p.text
    return p.json()


def tb(h, **kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    return client.get(f"/api/gl/trial-balance?{q}", headers=h).json()


def test_sales_invoice_posts_debtor_income_and_gst(h):
    uid = uuid.uuid4().hex[:5]
    party = "Gl Debtor " + uid
    client.post("/api/customers", json={"code": "GL" + uid.upper(), "name": party, "roles": ["shipper"], "data": {}}, headers=h)
    inv = invoice(h, party, "10000", intra=True)        # 10,000 + 18% (900 CGST + 900 SGST)
    led = lrep(h, party)
    assert len(led["entries"]) == 1 and led["entries"][0]["debit"] == "11800.00" and led["closing"] == "11800.00" and led["entries"][0]["number"] == inv["doc_number"]
    assert lrep(h, "Output CGST")["entries"][-1]["credit"] == "900.00" and lrep(h, "Output SGST")["entries"][-1]["credit"] == "900.00"
    inc = next(e for e in client.get("/api/gl/day-book", headers=h).json()["vouchers"] if e["number"] == inv["doc_number"])
    v = client.get(f"/api/gl/vouchers/{inc['id']}", headers=h).json()
    assert sum(D(e["debit"]) for e in v["entries"]) == sum(D(e["credit"]) for e in v["entries"]) == D("11800.00") and v["type"] == "SALES"
    inter = invoice(h, party, "1000")
    assert lrep(h, "Output IGST")["entries"][-1]["credit"] == "180.00" and inter["doc_number"]


def test_receipt_with_tds_and_round_off_posts_bank_tds_and_clears_the_debtor(h):
    uid = uuid.uuid4().hex[:5]
    party = "Gl Rcpt " + uid
    client.post("/api/customers", json={"code": "GR" + uid.upper(), "name": party, "roles": ["shipper"], "data": {}}, headers=h)
    inv = invoice(h, party, "1000", tax="18")          # 1,180.00
    pay = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": party, "method": "Cash", "amount": "1000", "kind": "part", "allocations": [{"document_id": inv["id"], "amount": "1000", "tds": "100", "round_off": "80"}]}, headers=h)
    assert pay.status_code == 201, pay.text
    led = lrep(h, party)
    assert led["closing"] == "0.00" and [e["credit"] for e in led["entries"]][-1] == "1180.00"
    assert lrep(h, "Cash in Hand")["entries"][-1]["debit"] == "1000.00"
    assert lrep(h, "TDS Receivable")["entries"][-1]["debit"] == "100.00"
    assert lrep(h, "Round Off")["entries"][-1]["debit"] == "80.00"
    assert client.post(f"/api/payments/{pay.json()['id']}/cancel", headers=h).status_code == 200
    assert lrep(h, party)["closing"] == "1180.00"       # cancelling the receipt takes it out of the ledger


def test_purchase_credit_note_and_vendor_ledger(h):
    uid = uuid.uuid4().hex[:5]
    vendor = "Gl Vendor " + uid
    client.post("/api/customers", json={"code": "GV" + uid.upper(), "name": vendor, "roles": ["carrier"], "data": {}}, headers=h)
    pi = invoice(h, vendor, "5000", doc_type="purchase_invoice")      # 5,000 + 900 IGST
    led = lrep(h, vendor)
    assert led["closing"] == "-5900.00" and led["entries"][0]["credit"] == "5900.00"
    assert lrep(h, "Input IGST")["entries"][-1]["debit"] == "900.00"
    cn = invoice(h, vendor, "1000", doc_type="purchase_credit_note", ref=pi["id"])
    assert lrep(h, vendor)["closing"] == "-4720.00" and cn["doc_number"]
    sale_cn_party = "Gl Debtor Cn " + uid
    client.post("/api/customers", json={"code": "GC" + uid.upper(), "name": sale_cn_party, "roles": ["shipper"], "data": {}}, headers=h)
    si = invoice(h, sale_cn_party, "1000")
    invoice(h, sale_cn_party, "500", doc_type="credit_note", ref=si["id"])
    assert lrep(h, sale_cn_party)["closing"] == "590.00"


def test_trial_balance_balances_and_group_summary_and_statements(h):
    t = tb(h)
    assert t["balanced"] and t["totals"]["closing_dr"] == t["totals"]["closing_cr"]
    assert any(r["kind"] == "group" and r["name"] == "SUNDRY DEBTORS" for r in t["rows"])
    sd = next(r for r in t["rows"] if r["kind"] == "group" and r["name"] == "SUNDRY DEBTORS")
    gs = tb(h, group_id=sd["id"])
    assert gs["rows"] and all(r["name"] == "SUNDRY DEBTORS" or r["kind"] == "ledger" for r in gs["rows"])
    pl = client.get("/api/gl/profit-loss", headers=h).json()
    assert D(pl["net_profit"]) == D(pl["total_income"]) - D(pl["total_expenses"]) and D(pl["total_income"]) > 0
    bs = client.get("/api/gl/balance-sheet", headers=h).json()
    assert bs["balanced"] and D(bs["total_assets"]) == D(bs["total_right"])
    far = tb(h, date_from="2099-01-01")
    assert far["totals"]["debit"] == "0.00"      # nothing happens in a future period, balances carry forward as opening


def test_manual_journal_and_contra_vouchers_and_guards(h):
    cash, capital = ledger_of(h, "Cash in Hand"), ledger_of(h, "Capital Account")
    exp = ledger_of(h, "Other Direct Expenses")
    uid = uuid.uuid4().hex[:4]
    bad = client.post("/api/gl/vouchers", json={"voucher_type": "JOURNAL", "lines": [{"ledger_id": cash["id"], "debit": "100"}, {"ledger_id": capital["id"], "credit": "90"}]}, headers=h)
    assert bad.status_code == 422 and "balance" in bad.json()["detail"]
    ok = client.post("/api/gl/vouchers", json={"voucher_type": "JOURNAL", "narration": "Capital introduced " + uid, "lines": [{"ledger_id": cash["id"], "debit": "5000"}, {"ledger_id": capital["id"], "credit": "5000"}]}, headers=h)
    assert ok.status_code == 201 and ok.json()["number"].startswith("JV/") and ok.json()["total"] == "5000.00"
    assert client.post("/api/gl/vouchers", json={"voucher_type": "JOURNAL", "lines": [{"ledger_id": exp["id"], "debit": "10", "credit": "10"}, {"ledger_id": capital["id"], "credit": "10"}]}, headers=h).status_code == 422
    bank = client.post("/api/coa/ledgers", json={"group_id": next(g for g in client.get("/api/coa/tree", headers=h).json()["groups"][0]["groups"][0]["groups"] if g["name"] == "BANK ACCOUNTS")["id"], "name": "Gl Test Bank " + uid}, headers=h).json()
    c_bad = client.post("/api/gl/vouchers", json={"voucher_type": "CONTRA", "lines": [{"ledger_id": exp["id"], "debit": "10"}, {"ledger_id": cash["id"], "credit": "10"}]}, headers=h)
    assert c_bad.status_code == 422
    c_ok = client.post("/api/gl/vouchers", json={"voucher_type": "CONTRA", "lines": [{"ledger_id": bank["id"], "debit": "700"}, {"ledger_id": cash["id"], "credit": "700"}]}, headers=h)
    assert c_ok.status_code == 201 and c_ok.json()["number"].startswith("CT/")
    assert lrep(h, "Gl Test Bank " + uid)["closing"] == "700.00"
    assert any(v["id"] == ok.json()["id"] for v in client.get("/api/gl/vouchers?voucher_type=JOURNAL", headers=h).json())
    assert client.post(f"/api/gl/vouchers/{c_ok.json()['id']}/cancel", headers=h).status_code == 200
    assert lrep(h, "Gl Test Bank " + uid)["closing"] == "0.00"
    assert tb(h)["balanced"]


def test_opening_balance_flows_into_the_ledger_and_rebuild_is_repeatable(h):
    uid = uuid.uuid4().hex[:4]
    g = next(x for x in client.get("/api/coa/tree", headers=h).json()["groups"][0]["groups"][0]["groups"] if x["name"] == "LOANS & ADVANCES")
    l = client.post("/api/coa/ledgers", json={"group_id": g["id"], "name": "Gl Advance " + uid, "opening_balance": "2500", "opening_side": "Dr"}, headers=h).json()
    r = lrep(h, "Gl Advance " + uid)
    assert r["opening"] == "2500.00" and r["closing"] == "2500.00"
    before = client.get("/api/gl/day-book", headers=h).json()
    n1 = client.post("/api/gl/rebuild", headers=h).json()
    after = client.get("/api/gl/day-book", headers=h).json()
    assert n1["documents"] > 0 and len(before["vouchers"]) == len(after["vouchers"]) and before["total"] == after["total"]
    assert lrep(h, "Gl Advance " + uid)["closing"] == "2500.00"


def test_general_ledger_agrees_with_the_party_ledger_and_cash_book_reports(h):
    uid = uuid.uuid4().hex[:5]
    party = "Gl Recon " + uid
    client.post("/api/customers", json={"code": "RC" + uid.upper(), "name": party, "roles": ["shipper"], "data": {}}, headers=h)
    inv = invoice(h, party, "2000", tax="18")       # 2,360.00
    client.post("/api/payments", json={"direction": "RECEIPT", "party_name": party, "method": "Cash", "amount": "1000", "kind": "part", "allocations": [{"document_id": inv["id"], "amount": "1000"}]}, headers=h)
    pl = client.get(f"/api/accounts/party-ledger?party={party}", headers=h).json()
    assert D(pl["closing"]) == D(lrep(h, party)["closing"].replace(" Dr", "")) == D("1360.00")
    gl_cash_receipts = sum(D(e["debit"]) for e in lrep(h, "Cash in Hand")["entries"] if e["voucher_type"] == "RECEIPT")
    assert gl_cash_receipts >= D("1000.00")
