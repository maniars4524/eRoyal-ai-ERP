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
