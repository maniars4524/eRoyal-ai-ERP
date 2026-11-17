"""Calculations confirmed from the Interport sample invoices: INR rounds to the rupee, foreign currency does not, export of services carries no GST, Exempt lines."""
from decimal import Decimal

from tests.test_billing import client, h  # noqa: F401


def _doc(h, **kw):
    body = {"doc_type": "invoice", "party_name": "Sample Co", **kw}
    r = client.post("/api/documents", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_inr_tax_invoice_matches_the_sample_and_rounds_to_the_rupee(h):
    lines = [{"charge_name": n, "rate": rate, "tax_pct": "9" if False else "18", "hsn": hsn} for n, rate, hsn in (("TERMINAL HANDLING CHARGES", "9500", "996711"), ("BILL OF LADING CHARGES", "4200", "996799"), ("SEAL CHARGES", "550", "996799"), ("MANDATORY USER CHARGES", "165", "996799"), ("HANDLING CHARGES", "1300", "996799"), ("HAZ CARGO DOCUMENTATION", "2000", "996799"))]
    d = _doc(h, supply_type="intra", place_of_supply="33-Tamilnadu", lines=lines)
    assert Decimal(d["subtotal"]) == Decimal("17715.00") and Decimal(d["tax_total"]) == Decimal("3188.70")       # 9% CGST + 9% SGST = 18%
    assert d["tax_breakup"]["cgst"] == "1594.35" and d["tax_breakup"]["sgst"] == "1594.35"
    assert d["round_off"] == "0.30" and Decimal(d["grand_total"]) == Decimal("20904.00")                       # 20,903.70 rounds to 20,904.00


def test_foreign_currency_document_is_not_rounded(h):
    lines = [{"charge_name": "OCEAN FREIGHT", "ccy": "USD", "exch": "83", "rate": "6806", "tax_type": "Non-Taxable"}, {"charge_name": "HANDLING", "ccy": "USD", "exch": "83", "rate": "75", "tax_type": "Non-Taxable"}, {"charge_name": "MESSENGER", "ccy": "USD", "exch": "83", "rate": "22.25", "tax_type": "Non-Taxable"}]
    d = _doc(h, doc_type="credit_note", lines=lines)
    assert d["round_off"] == "0.00" and Decimal(d["grand_total"]) == Decimal("6903.25") * 83


def test_export_of_services_carries_no_gst_and_exempt_lines_are_accepted(h):
    lines = [{"charge_name": "OCEAN FREIGHT", "ccy": "USD", "exch": "83", "rate": "7125", "tax_type": "Exempt", "hsn": "996521"}, {"charge_name": "DELIVERY ORDER CHARGES", "ccy": "USD", "exch": "83", "rate": "75", "tax_type": "Taxable", "tax_pct": "18", "hsn": "996799"}]
    d = _doc(h, place_of_supply="Outside India", supply_type="inter", lines=lines)
    assert Decimal(d["tax_total"]) == 0 and d["round_off"] == "0.00" and Decimal(d["grand_total"]) == Decimal("7200") * 83
    assert [l["tax_type"] for l in d["lines"]] == ["Exempt", "Taxable"] and all(Decimal(l["tax_pct"]) == 0 for l in d["lines"])


def test_template_names_can_be_renamed_per_company(h):
    assert client.get("/api/company/template-names", headers=h).json() == {"one": "Template one", "two": "Template two"}
    r = client.put("/api/company/template-names", json={"one": "Standard", "two": "Interport style"}, headers=h)
    assert r.status_code == 200 and r.json() == {"one": "Standard", "two": "Interport style"}
    assert client.get("/api/company/template-names", headers=h).json()["two"] == "Interport style"
    assert client.put("/api/company/template-names", json={"one": "x" * 41}, headers=h).status_code == 422
    client.put("/api/company/template-names", json={"one": "", "two": ""}, headers=h)
    assert client.get("/api/company/template-names", headers=h).json() == {"one": "Template one", "two": "Template two"}


def test_overseas_debit_and_credit_notes_have_their_own_number_series(h):
    inr = [{"charge_name": "x", "rate": "100", "tax_pct": "18"}]
    usd = [{"charge_name": "x", "ccy": "USD", "exch": "83", "rate": "100", "tax_type": "Non-Taxable"}]

    inv = {}
    for k, ls in (("inr", inr), ("usd", usd)):
        i = _doc(h, party_name="Series Co " + k, lines=ls)
        assert client.post(f"/api/documents/{i['id']}/post", headers=h).status_code == 200
        inv[k] = i["id"]

    def posted(doc_type, lines):
        ref = {"ref_document_id": inv["usd" if lines is usd else "inr"]} if doc_type == "credit_note" else {}
        d = _doc(h, doc_type=doc_type, party_name="Series Co " + ("usd" if lines is usd else "inr"), lines=lines, **ref)
        r = client.post(f"/api/documents/{d['id']}/post", headers=h)
        assert r.status_code == 200, r.text
        return r.json()["doc_number"]
    dn_inr, dn_usd, cn_inr, cn_usd = posted("debit_note", inr), posted("debit_note", usd), posted("credit_note", inr), posted("credit_note", usd)
    assert dn_inr.startswith("DN/") and cn_inr.startswith("CN/")
    assert dn_usd.startswith("ODN/") and cn_usd.startswith("OCN/")
    assert posted("debit_note", usd) != dn_usd                                       # and they keep counting
    seqs = {s["doc_type"] for s in client.get("/api/sequences?limit=500", headers=h).json()["items"]}
    assert {"debit_note_overseas", "credit_note_overseas"} <= seqs                     # visible and configurable in Sequence Setting


def test_hawb_format_names_and_default_are_saved_per_company(h):
    base = client.get("/api/company/hawb-formats", headers=h).json()
    assert base == {"names": {"std": "Existing layout", "one": "Format one", "two": "Format two", "three": "Format three"}, "default": "std"}
    r = client.put("/api/company/hawb-formats", json={"names": {"one": "Sri Lankan", "two": "Typed", "three": "System"}, "default": "two"}, headers=h)
    assert r.status_code == 200 and r.json()["names"]["one"] == "Sri Lankan" and r.json()["default"] == "two"
    assert client.get("/api/company/hawb-formats", headers=h).json()["default"] == "two"
    assert client.put("/api/company/hawb-formats", json={"default": "nine"}, headers=h).status_code == 422
    assert client.put("/api/company/hawb-formats", json={"names": {"one": "x" * 41}}, headers=h).status_code == 422
    client.put("/api/company/hawb-formats", json={"names": {}, "default": "std"}, headers=h)
