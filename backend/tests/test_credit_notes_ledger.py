"""Credit notes (customer) against an invoice or the job, and purchase credit notes (PCN) against a purchase invoice: amounts, balances and the ledger, all from the live code path."""
import uuid
from decimal import Decimal

import pytest

from tests.test_billing import client, make_job
from tests.test_gl import D, h, invoice, lrep, tb  # noqa: F401


def party(h, role="shipper"):
    uid = uuid.uuid4().hex[:6]
    name = f"CN Party {uid}"
    assert client.post("/api/customers", json={"code": "CN" + uid.upper(), "name": name, "roles": [role], "data": {}}, headers=h).status_code in (200, 201)
    return name


def doc(h, doc_type, name, rate, ref=None, job=None, intra=False, tax="18", kind="Taxable", ccy="INR", exch="1", extra_lines=None, post=True):
    body = {"doc_type": doc_type, "party_name": name, "supply_type": "intra" if intra else "inter",
            "lines": [{"charge_name": "Ocean Freight", "qty": "1", "rate": rate, "tax_type": kind, "tax_pct": tax if kind == "Taxable" else "0", "ccy": ccy, "exch": exch}] + (extra_lines or [])}
    if ref:
        body["ref_document_id"] = ref
    if job:
        body["job_number"] = job
    r = client.post("/api/documents", json=body, headers=h)
    assert r.status_code == 201, r.text
    if not post:
        return r.json()
    p = client.post(f"/api/documents/{r.json()['id']}/post", headers=h)
    assert p.status_code == 200, p.text
    return p.json()


def balanced(h, number):
    v = next(e for e in client.get("/api/gl/day-book", headers=h).json()["vouchers"] if e["number"] == number)
    full = client.get(f"/api/gl/vouchers/{v['id']}", headers=h).json()
    assert sum(D(e["debit"]) for e in full["entries"]) == sum(D(e["credit"]) for e in full["entries"])
    return full


def test_credit_note_for_the_difference_against_an_invoice(h):
    name = party(h)
    inv = doc(h, "invoice", name, "10000", intra=True)                                   # 10,000 + 1,800 GST = 11,800
    assert lrep(h, name)["closing"] == "11800.00"
    cn = doc(h, "credit_note", name, "1500", ref=inv["id"], intra=True)                  # charged 1,500 too much: credit 1,500 + 270 GST = 1,770
    assert cn["doc_number"] and D(cn["grand_total"]) == D("1770.00")
    assert cn["ref_doc_number"] == inv["doc_number"]                                     # the credited invoice is written on the note
    assert lrep(h, name)["closing"] == "10030.00"                                        # the customer now owes 11,800 - 1,770
    again = client.get(f"/api/documents/{inv['id']}", headers=h).json()
    assert D(again["credited_amount"]) == D("1770.00") and D(again["outstanding"]) == D("10030.00")
    v = balanced(h, cn["doc_number"])
    assert v["type"] == "CREDIT_NOTE"
    assert lrep(h, "Output CGST")["entries"][-1]["debit"] == "135.00" and lrep(h, "Output SGST")["entries"][-1]["debit"] == "135.00"          # the GST of the credit is taken back
    assert lrep(h, name)["entries"][-1]["credit"] == "1770.00"
    big = doc(h, "credit_note", name, "20000", ref=inv["id"], intra=True, post=False)
    r = client.post(f"/api/documents/{big['id']}/post", headers=h)
    assert r.status_code == 422 and "exceeds the outstanding" in r.text                  # a note can never credit more than the invoice still holds


def test_credit_note_for_chosen_entries_of_an_invoice(h):
    name = party(h)
    inv = doc(h, "invoice", name, "5000", extra_lines=[{"charge_name": "THC", "qty": "2", "rate": "1000", "tax_type": "Taxable", "tax_pct": "18"}])        # 5,000 + 2,000 = 7,000 + 1,260
    cn = doc(h, "credit_note", name, "1000", ref=inv["id"])                              # entry based: the THC line, quantity 1, is credited
    assert D(cn["grand_total"]) == D("1180.00") and lrep(h, name)["closing"] == "7080.00"
    assert D(client.get(f"/api/documents/{inv['id']}", headers=h).json()["outstanding"]) == D("7080.00")


def test_credit_note_against_the_job_without_an_invoice_posts_and_reaches_the_ledger(h):
    job = make_job(h, with_charges=False)
    name = party(h, "agent")
    cn = doc(h, "credit_note", name, "6903.25", job=job, kind="Non-Taxable", ccy="USD", exch="83")        # the overseas credit note of the job (no invoice)
    assert cn["doc_number"].startswith("OCN/") and cn["status"] == "POSTED" and not cn["ref_document_id"]
    assert D(cn["grand_total"]) == D("6903.25") * 83
    led = lrep(h, name)
    assert led["entries"][-1]["credit"] == str(D("6903.25") * 83) and D(led["closing"]) == -D("6903.25") * 83      # the agent is credited
    balanced(h, cn["doc_number"])
    # without a job and without an invoice a credit note still cannot be posted
    orphan = doc(h, "credit_note", name, "10", post=False)
    r = client.post(f"/api/documents/{orphan['id']}/post", headers=h)
    assert r.status_code == 422 and "must reference" in r.text


def test_purchase_credit_note_for_the_additional_amount_of_a_purchase_invoice(h):
    vendor = party(h, "carrier")
    pi = doc(h, "purchase_invoice", vendor, "10000")                                     # the vendor bill entered with 10,000 + 1,800
    assert lrep(h, vendor)["closing"] == "-11800.00"
    pcn = doc(h, "purchase_credit_note", vendor, "2000", ref=pi["id"])                   # 2,000 was entered in excess: credit 2,000 + 360
    assert pcn["doc_number"] and D(pcn["grand_total"]) == D("2360.00") and pcn["ref_doc_number"] == pi["doc_number"]
    assert lrep(h, vendor)["closing"] == "-9440.00"                                      # what is payable to the vendor falls to 11,800 - 2,360
    assert lrep(h, "Input IGST")["entries"][-1]["credit"] == "360.00"                    # the input tax credit is reduced
    after = client.get(f"/api/documents/{pi['id']}", headers=h).json()
    assert D(after["credited_amount"]) == D("2360.00") and D(after["outstanding"]) == D("9440.00")
    v = balanced(h, pcn["doc_number"])
    assert v["type"] == "PURCHASE_CREDIT_NOTE"
    too_much = doc(h, "purchase_credit_note", vendor, "50000", ref=pi["id"], post=False)
    assert client.post(f"/api/documents/{too_much['id']}/post", headers=h).status_code == 422
    # a purchase credit note always names the purchase invoice it credits (not a job alone)
    job = make_job(h, with_charges=False)
    solo = doc(h, "purchase_credit_note", vendor, "10", job=job, post=False)
    r = client.post(f"/api/documents/{solo['id']}/post", headers=h)
    assert r.status_code == 422 and "purchase invoice" in r.text


def test_cancelling_a_credit_note_gives_the_balance_back_and_takes_it_out_of_the_ledger(h):
    name = party(h)
    inv = doc(h, "invoice", name, "1000")
    cn = doc(h, "credit_note", name, "400", ref=inv["id"])
    before = D(lrep(h, name)["closing"])
    assert client.post(f"/api/documents/{cn['id']}/cancel", headers=h).status_code == 200
    assert D(lrep(h, name)["closing"]) == before + D("472.00")                           # the credit is out of the ledger again
    assert D(client.get(f"/api/documents/{inv['id']}", headers=h).json()["outstanding"]) == D("1180.00")


def test_trial_balance_still_balances_after_all_of_them(h):
    t = tb(h)
    assert t["balanced"] and t["totals"]["closing_dr"] == t["totals"]["closing_cr"]
