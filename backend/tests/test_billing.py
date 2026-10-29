import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.billing import fiscal_year
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


def make_job(h, with_charges=True):
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    if with_charges:
        lines = [
            {"charge_name": "Ocean Freight", "qty": "2", "hsn": "996512", "buy": {"ccy": "USD", "exch": "83.1", "rate": "680", "tax_type": "Non-Taxable", "party": "MSC"}, "sell": {"ccy": "USD", "exch": "83.5", "rate": "850", "tax_type": "Taxable", "tax_pct": "18", "party": "Acme"}},
            {"charge_name": "THC", "qty": "2", "hsn": "996719", "buy": {"ccy": "INR", "rate": "5200", "tax_type": "Taxable", "tax_pct": "18", "party": "Port"}, "sell": {"ccy": "INR", "rate": "6500", "tax_type": "Taxable", "tax_pct": "18", "party": "Acme"}},
        ]
        assert client.put(f"/api/shipments/by-number/{job['job_number']}/charges", json={"lines": lines}, headers=h).status_code == 200
    return job["job_number"]


def test_fiscal_year_boundaries():
    assert fiscal_year(date(2026, 3, 31)) == "25-26" and fiscal_year(date(2026, 4, 1)) == "26-27" and fiscal_year(date(2026, 12, 31)) == "26-27"


def test_invoice_from_job_is_calculated_on_the_server_and_numbered_at_posting(h):
    job = make_job(h)
    r = client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "invoice"}, headers=h)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "DRAFT" and doc["doc_number"] is None and doc["job_number"] == job
    # 2 x 850 USD x 83.5 = 141,950.00 (+18% = 25,551.00); 2 x 6,500 = 13,000.00 (+18% = 2,340.00)
    assert doc["subtotal"] == "154950.00" and doc["tax_total"] == "27891.00" and doc["grand_total"] == "182841.00"
    assert doc["tax_breakup"]["igst"] == "27891.00"
    posted = client.post(f"/api/documents/{doc['id']}/post", headers=h).json()
    fy = fiscal_year(date.today())
    assert posted["status"] == "POSTED" and posted["doc_number"].startswith(f"INV/{fy}/") and posted["outstanding"] == "182841.00" and posted["payment_status"] == "UNPAID"


def test_duplicate_invoice_for_same_job_and_party_is_blocked(h):
    job = make_job(h)
    assert client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "invoice"}, headers=h).status_code == 201
    dup = client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "invoice"}, headers=h)
    assert dup.status_code == 409 and "already exists" in dup.json()["detail"]


def test_purchase_invoice_per_vendor(h):
    job = make_job(h)
    many = client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "purchase_invoice"}, headers=h)
    assert many.status_code == 422 and "MSC" in many.json()["detail"] and "Port" in many.json()["detail"]
    pi = client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "purchase_invoice", "party": "MSC"}, headers=h)
    assert pi.status_code == 201 and pi.json()["party_name"] == "MSC" and pi.json()["subtotal"] == "113016.00" and pi.json()["tax_total"] == "0.00"
    assert client.post("/api/documents/from-job", json={"job_number": make_job(h, False), "doc_type": "invoice"}, headers=h).status_code == 422


def test_posted_documents_are_immutable_and_numbers_are_gap_free(h):
    numbers = []
    for _ in range(3):
        d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Gap Co", "lines": [{"charge_name": "x", "rate": "100", "tax_pct": "18"}]}, headers=h).json()
        numbers.append(client.post(f"/api/documents/{d['id']}/post", headers=h).json()["doc_number"])
    seq = [int(n.rsplit("/", 1)[1]) for n in numbers]
    assert seq == list(range(seq[0], seq[0] + 3))
    assert client.patch(f"/api/documents/{d['id']}", json={"party_name": "Changed"}, headers=h).status_code == 409
    assert client.post(f"/api/documents/{d['id']}/post", headers=h).status_code == 409


def test_concurrent_posting_never_duplicates_numbers(h):
    ids = [client.post("/api/documents", json={"doc_type": "debit_note", "party_name": "Race", "lines": [{"charge_name": "x", "rate": "10"}]}, headers=h).json()["id"] for _ in range(8)]
    results = []

    def post(doc_id):
        c = TestClient(app)
        results.append(c.post(f"/api/documents/{doc_id}/post", headers=h).json()["doc_number"])

    threads = [threading.Thread(target=post, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 8 and len(set(results)) == 8
    nums = sorted(int(n.rsplit("/", 1)[1]) for n in results)
    assert nums == list(range(nums[0], nums[0] + 8))


def test_post_validation(h):
    empty = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "E"}, headers=h).json()
    assert client.post(f"/api/documents/{empty['id']}/post", headers=h).status_code == 422
    noparty = client.post("/api/documents", json={"doc_type": "invoice", "lines": [{"rate": "5"}]}, headers=h).json()
    assert client.post(f"/api/documents/{noparty['id']}/post", headers=h).status_code == 422
    assert client.post("/api/documents", json={"doc_type": "invoice", "party_name": "x", "lines": [{"ccy": "USD", "exch": "0", "rate": "1"}]}, headers=h).status_code == 422
    assert client.post("/api/documents", json={"doc_type": "bogus"}, headers=h).status_code == 422


def posted_invoice(h, rate="1000", party="Pay Co"):
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": party, "lines": [{"charge_name": "x", "rate": rate, "tax_pct": "0", "tax_type": "Non-Taxable"}]}, headers=h).json()
    return client.post(f"/api/documents/{d['id']}/post", headers=h).json()


def test_receipt_allocation_partial_then_full_and_overpayment_rejected(h):
    inv = posted_invoice(h)
    pay = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": "Pay Co", "amount": "400", "allocations": [{"document_id": inv["id"], "amount": "400"}]}, headers=h)
    assert pay.status_code == 201 and pay.json()["pay_number"].startswith("RCP/")
    after = client.get(f"/api/documents/{inv['id']}", headers=h).json()
    assert after["paid_amount"] == "400.00" and after["outstanding"] == "600.00" and after["payment_status"] == "PARTIAL"
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "700", "allocations": [{"document_id": inv["id"], "amount": "700"}]}, headers=h).status_code == 422
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "100", "allocations": [{"document_id": inv["id"], "amount": "200"}]}, headers=h).status_code == 422
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "600", "allocations": [{"document_id": inv["id"], "amount": "600"}]}, headers=h).status_code == 201
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["payment_status"] == "PAID"
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "1", "allocations": [{"document_id": inv["id"], "amount": "1"}]}, headers=h).status_code == 422


def test_payment_direction_must_match_document_side(h):
    inv = posted_invoice(h)
    assert client.post("/api/payments", json={"direction": "PAYMENT", "amount": "10", "allocations": [{"document_id": inv["id"], "amount": "10"}]}, headers=h).status_code == 422
    draft = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "D", "lines": [{"rate": "5"}]}, headers=h).json()
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "5", "allocations": [{"document_id": draft["id"], "amount": "5"}]}, headers=h).status_code == 422


def test_idempotency_key_prevents_double_payment_even_concurrently(h):
    inv = posted_invoice(h, "500")
    body = {"direction": "RECEIPT", "amount": "100", "allocations": [{"document_id": inv["id"], "amount": "100"}]}
    key = {**h, "Idempotency-Key": "k-" + uuid.uuid4().hex}
    first = client.post("/api/payments", json=body, headers=key)
    again = client.post("/api/payments", json=body, headers=key)
    assert first.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["paid_amount"] == "100.00"
    key2 = {**h, "Idempotency-Key": "k-" + uuid.uuid4().hex}
    out = []
    threads = [threading.Thread(target=lambda: out.append(TestClient(app).post("/api/payments", json=body, headers=key2).json()["id"])) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 1
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["paid_amount"] == "200.00"


def test_concurrent_distinct_payments_cannot_overpay(h):
    inv = posted_invoice(h, "100")
    out = []
    body = {"direction": "RECEIPT", "amount": "80", "allocations": [{"document_id": inv["id"], "amount": "80"}]}
    threads = [threading.Thread(target=lambda: out.append(TestClient(app).post("/api/payments", json=body, headers=h).status_code)) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(out) == [201, 422, 422]
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["paid_amount"] == "80.00"


def test_cancel_payment_releases_allocation(h):
    inv = posted_invoice(h, "300")
    pay = client.post("/api/payments", json={"direction": "RECEIPT", "amount": "300", "allocations": [{"document_id": inv["id"], "amount": "300"}]}, headers=h).json()
    assert client.post(f"/api/documents/{inv['id']}/cancel", headers=h).status_code == 409
    assert client.post(f"/api/payments/{pay['id']}/cancel", headers=h).json()["status"] == "CANCELLED"
    assert client.post(f"/api/payments/{pay['id']}/cancel", headers=h).status_code == 409
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["outstanding"] == "300.00"
    assert client.post(f"/api/documents/{inv['id']}/cancel", headers=h).json()["status"] == "CANCELLED"


def test_credit_note_reduces_outstanding_and_cannot_exceed_it(h):
    inv = posted_invoice(h, "1000")
    cn = client.post("/api/documents", json={"doc_type": "credit_note", "party_name": "Pay Co", "ref_document_id": inv["id"], "lines": [{"charge_name": "adj", "rate": "250", "tax_type": "Non-Taxable"}]}, headers=h).json()
    posted = client.post(f"/api/documents/{cn['id']}/post", headers=h).json()
    assert posted["doc_number"].startswith("CN/")
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["outstanding"] == "750.00"
    big = client.post("/api/documents", json={"doc_type": "credit_note", "party_name": "Pay Co", "ref_document_id": inv["id"], "lines": [{"rate": "900", "tax_type": "Non-Taxable"}]}, headers=h).json()
    assert client.post(f"/api/documents/{big['id']}/post", headers=h).status_code == 422
    assert client.post("/api/documents", json={"doc_type": "credit_note", "party_name": "x", "ref_document_id": cn["id"], "lines": [{"rate": "1"}]}, headers=h).status_code == 422
    assert client.post(f"/api/documents/{inv['id']}/cancel", headers=h).status_code == 409
    assert client.post(f"/api/documents/{cn['id']}/cancel", headers=h).json()["status"] == "CANCELLED"
    assert client.get(f"/api/documents/{inv['id']}", headers=h).json()["outstanding"] == "1000.00"


def test_summary_reflects_posted_documents(h):
    s = client.get("/api/documents/summary", headers=h).json()
    assert Decimal(s["receivable_outstanding"]) > 0 and s["open_invoices"] >= 1


def test_tenant_isolation_and_permissions(world, admin, h):
    inv = posted_invoice(h)
    demo = hdr(world["demo"], portal="demo")
    assert client.get(f"/api/documents/{inv['id']}", headers=demo).status_code in (403, 404)
    assert client.get("/api/documents", headers=demo).status_code in (200, 403)
    if client.get("/api/documents", headers=demo).status_code == 200:
        assert inv["id"] not in [d["id"] for d in client.get("/api/documents", headers=demo).json()]
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER", "OPERATIONS"):
        client.post("/api/users", json={"username": f"{role[:2].lower()}{tag}", "email": f"{role[:2].lower()}{tag}@b.test", "password": PASSWORD, "first_name": role, "role": role}, headers=h)
    viewer, ops = hdr(f"vi{tag}", admin["license"]), hdr(f"op{tag}", admin["license"])
    assert client.get(f"/api/documents/{inv['id']}", headers=viewer).status_code == 200
    assert client.post("/api/documents", json={"doc_type": "invoice", "party_name": "x"}, headers=viewer).status_code == 403
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "x", "lines": [{"rate": "5"}]}, headers=ops)
    assert d.status_code == 201
    assert client.post(f"/api/documents/{d.json()['id']}/post", headers=ops).status_code == 403
    assert client.post("/api/payments", json={"direction": "RECEIPT", "amount": "1"}, headers=ops).status_code == 403
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers=hdr(world["dev"], portal="developer")).status_code == 403


def test_editing_a_draft_replaces_lines_and_recalculates(h):
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Edit Co", "lines": [{"charge_name": "a", "rate": "100", "tax_pct": "18"}, {"charge_name": "b", "rate": "50", "tax_pct": "0", "tax_type": "Non-Taxable"}]}, headers=h).json()
    assert d["subtotal"] == "150.00" and d["grand_total"] == "168.00"
    upd = client.patch(f"/api/documents/{d['id']}", json={"party_name": "Edit Co Ltd", "supply_type": "intra", "lines": [{"charge_name": "c", "rate": "200", "tax_pct": "18"}]}, headers=h)
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert [l["charge_name"] for l in body["lines"]] == ["c"] and body["grand_total"] == "236.00" and body["party_name"] == "Edit Co Ltd"
    assert body["tax_breakup"] == {"cgst": "18.00", "sgst": "18.00", "igst": "0.00"}
    again = client.patch(f"/api/documents/{d['id']}", json={"lines": [{"charge_name": "x", "rate": "1"}, {"charge_name": "y", "rate": "2"}, {"charge_name": "z", "rate": "3"}]}, headers=h)
    assert again.status_code == 200 and len(again.json()["lines"]) == 3


def test_proforma_must_be_confirmed_before_the_tax_invoice_and_einvoice_is_optional(h):
    from app.job_lifecycle import setting_enabled  # noqa: F401

    cust = client.post("/api/customers", json={"code": "PFC" + uuid.uuid4().hex[:4].upper(), "name": "Proforma Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    line = {"charge_name": "Ocean Freight", "description": "", "hsn": "996521", "uom": "Per Shipment", "qty": "1", "ccy": "INR", "exch": "1", "rate": "10000", "tax_type": "Taxable", "tax_pct": "18"}
    mk = lambda: client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Proforma Customer", "customer_id": cust["id"], "party_gstin": "27ABCDE1234F1Z5", "lines": [line]}, headers=h).json()
    d0 = mk()
    assert client.post(f"/api/documents/{d0['id']}/post", headers=h).status_code == 200      # the rule is off until the company switches it on
    client.post("/api/masters/setting", json={"code": "INVOICE_REQUIRES_PROFORMA", "name": "Invoice requires a confirmed proforma", "data": {"enabled": "yes"}}, headers=h)
    d = mk()
    assert d["proforma_required"] is True
    blocked = client.post(f"/api/documents/{d['id']}/post", headers=h)
    assert blocked.status_code == 409 and "Proforma" in blocked.json()["detail"]
    c = client.post(f"/api/documents/{d['id']}/confirm", headers=h)
    assert c.status_code == 200 and c.json()["status"] == "CONFIRMED" and c.json()["header"]["proforma_no"].startswith("PF/")
    assert client.patch(f"/api/documents/{d['id']}", json={"remarks": "x"}, headers=h).status_code == 409   # a confirmed proforma is locked
    assert client.post(f"/api/documents/{d['id']}/e-invoice", headers=h).status_code == 409                  # not a posted tax invoice yet
    back = client.post(f"/api/documents/{d['id']}/reopen-proforma", headers=h)
    assert back.status_code == 200 and back.json()["status"] == "DRAFT"
    client.post(f"/api/documents/{d['id']}/confirm", headers=h)
    posted = client.post(f"/api/documents/{d['id']}/post", headers=h)
    assert posted.status_code == 200 and posted.json()["status"] == "POSTED" and posted.json()["header"]["proforma_no"]
    no_gstin = client.post(f"/api/documents/{d['id']}/e-invoice", headers=h)
    assert no_gstin.status_code == 422 and "GSTIN" in no_gstin.json()["detail"]                              # the company GSTIN is needed
    client.put("/api/company/doc-profile", json={"gstin": "33AAACS1234A1Z5"}, headers=h)
    e = client.post(f"/api/documents/{d['id']}/e-invoice", headers=h)
    assert e.status_code == 200, e.text
    ei = e.json()["header"]["einvoice"]
    assert len(ei["irn"]) == 64 and ei["qr"].startswith("data:image/svg+xml;base64,") and ei["registered"] is False and ei["qr_payload"]["DocNo"] == posted.json()["doc_number"]
    assert client.post(f"/api/documents/{d['id']}/e-invoice", headers=h).json()["header"]["einvoice"]["irn"] == ei["irn"]   # one IRN per invoice


def test_invoice_kinds_from_the_charge_entries_and_separate_pure_agent_and_rcm(h):
    cust = client.post("/api/customers", json={"code": "KND" + uuid.uuid4().hex[:4].upper(), "name": "Kind Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    jn = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "SGSIN", "customer_id": cust["id"]}, headers=h).json()["job_number"]
    side = lambda tt, pct="0": {"ccy": "INR", "rate": "1000", "tax_type": tt, "tax_pct": pct, "party": "Kind Customer"}
    rows = [("Ocean Freight", "Taxable", "18"), ("Bill of Lading Fee", "Non-Taxable", "0"), ("Port Fee Reimbursed", "Pure Agent", "0"), ("Foreign Agent Fee", "RCM", "18")]
    lines = [{"charge_name": n, "qty": "1", "hsn": "996799", "sell": side(t, p), "buy": {"ccy": "INR", "rate": "0", "tax_type": "Non-Taxable", "tax_pct": "0", "party": ""}} for n, t, p in rows]
    assert client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": lines}, headers=h).status_code == 200
    r = client.post("/api/documents/from-job", json={"job_number": jn, "doc_type": "invoice"}, headers=h)
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["invoice_kind"] == "TAX" and {l["tax_type"] for l in first["lines"]} == {"Taxable", "Non-Taxable"}     # taxable and non-taxable travel together
    assert float(first["tax_total"]) == 180.0 and float(first["grand_total"]) == 2180.0
    kinds = {x["invoice_kind"] for x in first["also_created"]}
    assert kinds == {"PURE_AGENT", "RCM"}                                                                                # each on its own invoice
    pa = client.get(f"/api/documents/{[x for x in first['also_created'] if x['invoice_kind'] == 'PURE_AGENT'][0]['id']}", headers=h).json()
    assert float(pa["tax_total"]) == 0 and pa["lines"][0]["tax_type"] == "Pure Agent"
    rcm = client.get(f"/api/documents/{[x for x in first['also_created'] if x['invoice_kind'] == 'RCM'][0]['id']}", headers=h).json()
    assert float(rcm["tax_total"]) == 0 and rcm["lines"][0]["tax_pct"] in ("18.00", "18")                                 # tax is the recipient's: not added, rate kept for information
    again = client.post("/api/documents/from-job", json={"job_number": jn, "doc_type": "invoice"}, headers=h)
    assert again.status_code == 409
    mixed = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Kind Customer", "lines": [{"charge_name": "A", "hsn": "9967", "qty": "1", "rate": "10", "tax_type": "Taxable", "tax_pct": "18"}, {"charge_name": "B", "hsn": "9967", "qty": "1", "rate": "10", "tax_type": "Pure Agent"}]}, headers=h)
    assert mixed.status_code == 422 and "own invoice" in mixed.json()["detail"]
    only_non = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Kind Customer", "lines": [{"charge_name": "A", "hsn": "9967", "qty": "1", "rate": "10", "tax_type": "Non-Taxable"}]}, headers=h).json()
    assert only_non["invoice_kind"] == "NON_TAXABLE"


def test_a_document_lists_its_receipts_with_transaction_details_and_partial_payments_make_several(h):
    cust = client.post("/api/customers", json={"code": "RCT" + uuid.uuid4().hex[:4].upper(), "name": "Receipt Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    line = {"charge_name": "Freight", "description": "", "hsn": "996521", "uom": "Per Shipment", "qty": "1", "ccy": "INR", "exch": "1", "rate": "1000", "tax_type": "Non-Taxable", "tax_pct": "0"}
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Receipt Customer", "customer_id": cust["id"], "lines": [line]}, headers=h).json()
    client.post(f"/api/documents/{d['id']}/confirm", headers=h)   # the proforma rule may be on for this company
    assert client.post(f"/api/documents/{d['id']}/post", headers=h).status_code == 200
    for amt, ref in (("400", "UTR-1"), ("600", "UTR-2")):
        r = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": "Receipt Customer", "method": "Bank Transfer", "reference": ref, "amount": amt, "allocations": [{"document_id": d["id"], "amount": amt}]}, headers=h)
        assert r.status_code == 201, r.text
    info = client.get(f"/api/documents/{d['id']}/payments", headers=h).json()
    assert [p["reference"] for p in info["payments"]] == ["UTR-1", "UTR-2"] and [p["applied_to_this"] for p in info["payments"]] == ["400.00", "600.00"]
    assert float(info["outstanding"]) == 0 and info["payments"][0]["against"][0]["doc_number"] == info["doc_number"]


def test_receipt_modes_need_their_transaction_details_full_vs_part_and_the_voucher(h):
    cust = client.post("/api/customers", json={"code": "TXN" + uuid.uuid4().hex[:4].upper(), "name": "Txn Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    line = lambda rate: {"charge_name": "Freight", "description": "", "hsn": "996521", "uom": "Per Shipment", "qty": "1", "ccy": "INR", "exch": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0"}
    docs = []
    for rate in ("1000", "500"):
        d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Txn Customer", "customer_id": cust["id"], "lines": [line(rate)]}, headers=h).json()
        client.post(f"/api/documents/{d['id']}/confirm", headers=h)
        assert client.post(f"/api/documents/{d['id']}/post", headers=h).status_code == 200
        docs.append(d["id"])
    pay = lambda **b: client.post("/api/payments", json={"direction": "RECEIPT", "party_name": "Txn Customer", **b}, headers=h)
    assert pay(method="Cheque", amount="100", allocations=[{"document_id": docs[0], "amount": "100"}]).status_code == 422                  # cheque number and date are needed
    assert pay(method="NEFT", amount="100", allocations=[{"document_id": docs[0], "amount": "100"}]).status_code == 422                    # UTR is needed
    assert pay(method="NEFT", reference="UTR9", amount="100", kind="full", allocations=[{"document_id": docs[0], "amount": "100"}]).status_code == 422   # not the whole balance
    part = pay(method="Cheque", amount="400", details={"cheque_no": "123456", "cheque_date": "2026-10-01", "bank_name": "HDFC", "account": "HDFC Current"}, allocations=[{"document_id": docs[0], "amount": "400"}])
    assert part.status_code == 201 and part.json()["details"]["settlement"] == "PART" and part.json()["details"]["cheque_no"] == "123456"
    both = pay(method="NEFT", reference="UTR-ALL", amount="1100", kind="full", allocations=[{"document_id": docs[0], "amount": "600"}, {"document_id": docs[1], "amount": "500"}])    # one payment, several invoices
    assert both.status_code == 201 and both.json()["details"]["settlement"] == "FULL"
    v = client.get(f"/api/payments/{part.json()['id']}/voucher", headers=h).json()
    assert v["lines"][0]["total"] == "1000.00" and v["lines"][0]["applied"] == "400.00" and v["lines"][0]["balance_after"] == "600.00"
    v2 = client.get(f"/api/payments/{both.json()['id']}/voucher", headers=h).json()
    assert len(v2["lines"]) == 2 and v2["lines"][0]["balance_after"] == "0.00"
    led = client.get("/api/accounts/party-ledger", params={"party": "Txn Customer"}, headers=h).json()
    assert float(led["closing"]) == 0 and len(led["entries"]) == 4 and float(led["total_debit"]) == 1500 and float(led["total_credit"]) == 1500
    cb = client.get("/api/accounts/cash-book", headers=h).json()
    assert any(e["reference"] == "UTR-ALL" and e["book"] == "bank" for e in cb["entries"]) and any(e["reference"] == "123456" for e in cb["entries"])
    ar = client.get("/api/accounts/outstanding", params={"side": "AR"}, headers=h).json()
    assert all(p["party"] != "Txn Customer" for p in ar["parties"])                                                                         # fully paid: not outstanding


def test_job_profit_and_loss_has_provisional_actual_tax_revenue_and_expense(h):
    cust = client.post("/api/customers", json={"code": "PNL" + uuid.uuid4().hex[:4].upper(), "name": "Pnl Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    jn = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "SGSIN", "customer_id": cust["id"]}, headers=h).json()["job_number"]
    sell = {"ccy": "INR", "rate": "10000", "tax_type": "Taxable", "tax_pct": "18", "party": "Pnl Customer"}
    buy = {"ccy": "INR", "rate": "7000", "tax_type": "Taxable", "tax_pct": "18", "party": "MSC Lines"}
    client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": [{"charge_name": "Ocean Freight", "qty": "1", "hsn": "996521", "sell": sell, "buy": buy}]}, headers=h)
    r = client.get(f"/api/accounts/job/{jn}/profit-loss", headers=h).json()
    assert r["totals"]["p_sale"] == "10000.00" and r["totals"]["p_cost"] == "7000.00" and r["totals"]["p_gp"] == "3000.00" and r["provisional_pct"] == "30.00"
    assert r["tax"] == {"sale": "1800.00", "cost": "1260.00", "net": "540.00"} and r["revenue"][0]["bill_to"] == "Pnl Customer" and r["expense"][0]["vendor"] == "MSC Lines" and r["totals"]["a_sale"] == "0.00"


def test_tds_deducted_by_the_customer_settles_the_invoice_but_is_not_cash(h):
    cust = client.post("/api/customers", json={"code": "TDS" + uuid.uuid4().hex[:4].upper(), "name": "Tds Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    line = {"charge_name": "Freight", "description": "", "hsn": "996521", "uom": "Per Shipment", "qty": "1", "ccy": "INR", "exch": "1", "rate": "10000", "tax_type": "Non-Taxable", "tax_pct": "0"}
    docs = []
    for _ in range(2):
        d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Tds Customer", "customer_id": cust["id"], "lines": [line]}, headers=h).json()
        client.post(f"/api/documents/{d['id']}/confirm", headers=h)
        assert client.post(f"/api/documents/{d['id']}/post", headers=h).status_code == 200
        docs.append(d["id"])
    pay = lambda **b: client.post("/api/payments", json={"direction": "RECEIPT", "party_name": "Tds Customer", **b}, headers=h)
    too_much = pay(method="NEFT", reference="U1", amount="9900", allocations=[{"document_id": docs[0], "amount": "9900", "tds": "200"}])
    assert too_much.status_code == 422                                                                   # amount + TDS beyond the invoice
    both = pay(method="NEFT", reference="UTR-TDS", amount="19800", kind="full", allocations=[{"document_id": docs[0], "amount": "9900", "tds": "100"}, {"document_id": docs[1], "amount": "9900", "tds": "100"}])
    assert both.status_code == 201, both.text
    b = both.json()
    assert b["amount"] == "19800.00" and b["tds"] == "200.00" and b["settled"] == "20000.00" and b["details"]["settlement"] == "FULL" and b["details"]["tds_total"] == "200.00"
    assert client.get(f"/api/documents/{docs[0]}", headers=h).json()["payment_status"] == "PAID"
    v = client.get(f"/api/payments/{b['id']}/voucher", headers=h).json()
    assert [l["tds"] for l in v["lines"]] == ["100.00", "100.00"] and [l["balance_after"] for l in v["lines"]] == ["0.00", "0.00"]
    led = client.get("/api/accounts/party-ledger", params={"party": "Tds Customer"}, headers=h).json()
    assert float(led["closing"]) == 0 and "TDS" in led["entries"][-1]["narration"]                       # the ledger credits cash + TDS
    cb = client.get("/api/accounts/cash-book", headers=h).json()
    assert [e["receipt"] for e in cb["entries"] if e["reference"] == "UTR-TDS"] == ["19800.00"]            # the bank only received the cash
    assert client.post(f"/api/payments/{b['id']}/cancel", headers=h).status_code == 200
    assert float(client.get(f"/api/documents/{docs[0]}", headers=h).json()["outstanding"]) == 10000       # cancelling gives back cash and TDS


def test_invoice_round_off_one_invoice_for_two_jobs_and_a_receipt_round_off(h):
    cust = client.post("/api/customers", json={"code": "MJB" + uuid.uuid4().hex[:4].upper(), "name": "Multi Job Customer", "roles": ["shipper"], "data": {}}, headers=h).json()
    jobs = []
    for pod in ("SGSIN", "AEJEA"):
        jn = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": pod, "customer_id": cust["id"]}, headers=h).json()["job_number"]
        side = {"ccy": "INR", "rate": "1000.40", "tax_type": "Taxable", "tax_pct": "18", "party": "Multi Job Customer"}
        client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": [{"charge_name": "Ocean Freight", "qty": "1", "hsn": "996521", "sell": side, "buy": {"ccy": "INR", "rate": "0", "tax_type": "Non-Taxable", "tax_pct": "0", "party": ""}}]}, headers=h)
        jobs.append(jn)
    one = client.post("/api/documents/from-job", json={"job_number": jobs[0], "doc_type": "invoice"}, headers=h).json()
    assert one["round_off"] == "-0.47" and one["grand_total"] == "1180.00"                      # 1000.40 + 180.07 = 1180.47 -> 1180 on the nearest rupee
    nope = client.post(f"/api/documents/{one['id']}/add-job", json={"job_number": jobs[0]}, headers=h)
    assert nope.status_code == 409                                                               # the same charges are not added twice
    bj = client.get("/api/documents/billing/billable-jobs", params={"party": "Multi Job Customer"}, headers=h).json()
    assert [b["job_number"] for b in bj] == [jobs[1]]
    both = client.post(f"/api/documents/{one['id']}/add-job", json={"job_number": jobs[1]}, headers=h)
    assert both.status_code == 200, both.text
    b = both.json()
    assert len(b["lines"]) == 2 and {l["job_number"] for l in b["lines"]} == {jobs[0], jobs[1]} and b["subtotal"] == "2000.80" and b["grand_total"] == "2361.00" and b["round_off"] == "0.06"
    off = client.patch(f"/api/documents/{one['id']}", json={"header": {"no_round_off": True}}, headers=h).json()
    assert off["round_off"] == "0.00" and off["grand_total"] == "2360.94"                        # the switch turns it off
    client.patch(f"/api/documents/{one['id']}", json={"header": {"no_round_off": False}}, headers=h)
    client.post(f"/api/documents/{one['id']}/confirm", headers=h)
    assert client.post(f"/api/documents/{one['id']}/post", headers=h).status_code == 200
    for jn in jobs:                                                                              # the invoice shows on both jobs
        assert one["id"] in [d["id"] for d in client.get("/api/documents", params={"job_number": jn}, headers=h).json()]
        pl = client.get(f"/api/accounts/job/{jn}/profit-loss", headers=h).json()
        assert pl["totals"]["a_sale"] == "1000.40"                                               # each job's actual sale is its own line
    # a small remainder written off on a receipt
    doc = client.get(f"/api/documents/{one['id']}", headers=h).json()
    tot = float(doc["outstanding"])
    r = client.post("/api/payments", json={"direction": "RECEIPT", "party_name": "Multi Job Customer", "method": "Cash", "amount": str(round(tot - 0.5, 2)), "kind": "full", "allocations": [{"document_id": one["id"], "amount": str(round(tot - 0.5, 2)), "round_off": "0.50"}]}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["details"]["settlement"] == "FULL" and r.json()["round_off"] == "0.50"
    assert client.get(f"/api/documents/{one['id']}", headers=h).json()["payment_status"] == "PAID"


def _vouchers(doc_id):
    from app.db import SessionLocal
    from app.models import AccVoucher

    db = SessionLocal()
    try:
        return db.query(AccVoucher).filter(AccVoucher.source_type == "document", AccVoucher.source_id == doc_id).count()
    finally:
        db.close()


def test_proforma_number_on_save_no_ledger_until_posted_and_cancel_empties_the_proforma(h):
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Proforma Co", "lines": [{"charge_name": "Freight", "rate": "1000", "tax_pct": "18"}]}, headers=h).json()
    assert d["status"] == "DRAFT" and d["doc_number"] is None and d["header"]["proforma_no"].startswith("PF/")            # saving gives the proforma number, never a tax-invoice number
    assert d["grand_total"] == "1180.00" and _vouchers(d["id"]) == 0                                                     # a draft proforma is in no ledger
    assert client.post(f"/api/documents/{d['id']}/confirm", headers=h).status_code == 200
    assert _vouchers(d["id"]) == 0                                                                                       # nor is a confirmed proforma
    posted = client.post(f"/api/documents/{d['id']}/post", headers=h).json()
    assert posted["doc_number"] and posted["status"] == "POSTED" and _vouchers(d["id"]) == 1                              # the tax invoice reaches the books only now
    cancelled = client.post(f"/api/documents/{d['id']}/cancel", headers=h).json()
    assert cancelled["status"] == "CANCELLED" and _vouchers(d["id"]) == 0 and cancelled["outstanding"] == "0.00"          # a cancelled invoice leaves the ledger

    p = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Cancel Co", "lines": [{"charge_name": "Freight", "rate": "5000", "tax_pct": "18"}, {"charge_name": "Docs", "rate": "500", "tax_pct": "0"}]}, headers=h).json()
    pf = p["header"]["proforma_no"]
    gone = client.post(f"/api/documents/{p['id']}/cancel", headers=h).json()
    assert gone["status"] == "CANCELLED" and gone["lines"] == [] and gone["grand_total"] == "0.00" and gone["subtotal"] == "0.00" and gone["tax_total"] == "0.00" and gone["outstanding"] == "0.00"
    assert gone["header"]["proforma_no"] == pf and gone["doc_number"] is None and _vouchers(p["id"]) == 0                 # entries deleted, the proforma number stays for the record
    listed = {x["id"]: x for x in client.get("/api/documents?doc_type=invoice", headers=h).json()}
    assert listed[p["id"]]["grand_total"] == "0.00" and listed[p["id"]]["outstanding"] == "0.00"


def test_a_billed_charge_cannot_be_billed_again_and_the_list_shows_every_job(h):
    jobs = []
    for _ in range(2):
        made = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
        jobs.append(made["job_number"])
    line = lambda j, n="Freight": {"charge_name": n, "rate": "100", "tax_pct": "0", "job_number": j, "qty": "1"}      # noqa: E731
    one = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Twice Co", "lines": [line(jobs[0]), line(jobs[1])]}, headers=h)
    assert one.status_code == 201, one.text
    assert one.json()["job_numbers"] == sorted(jobs)                                                                    # one invoice for two jobs: both numbers
    dup = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Twice Co", "lines": [line(jobs[0])]}, headers=h)
    assert dup.status_code == 409 and "already billed" in dup.json()["detail"]                                         # the same entry cannot go on a second invoice
    other = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Twice Co", "lines": [line(jobs[0], "Documentation")]}, headers=h)
    assert other.status_code == 201                                                                                      # a different charge of the job is still free
    # cancelling the first invoice frees its entries again
    assert client.post(f"/api/documents/{one.json()['id']}/cancel", headers=h).status_code == 200
    again = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Twice Co", "lines": [line(jobs[0])]}, headers=h)
    assert again.status_code == 201
    listed = client.get("/api/documents?doc_type=invoice&q=" + jobs[1], headers=h).json()
    assert all("job_numbers" in d for d in listed)
