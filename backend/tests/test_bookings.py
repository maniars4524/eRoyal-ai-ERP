import threading
import uuid
from datetime import date, timedelta

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


def draft(h, **extra):
    body = {"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Acme Exports", "carrier": "MSC", "carrier_booking_no": "MSC123", "vessel": "MSC ROMA / 254W",
            "pol": "INMAA", "pod": "BEANR", "etd": str(date.today() + timedelta(days=10)), "eta": str(date.today() + timedelta(days=30)), "si_cutoff": str(date.today() + timedelta(days=1)), "vgm_cutoff": str(date.today() + timedelta(days=2)),
            "data": {"pcs": "120", "gwt": "24000", "consignee": "Solvay SA", "marks": "Soda ash", "charges": [{"type": "AP", "code": "OFR", "qty": "2", "rate": "680", "currency": "USD"}]}, **extra}
    r = client.post("/api/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_draft_roundtrip_and_validation(h):
    b = draft(h)
    assert b["status"] == "DRAFT" and b["number"].startswith("BKG/") and b["data"]["charges"][0]["code"] == "OFR"
    assert client.post("/api/bookings", json={"mode": "Teleport"}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"customer_id": 99999999}, headers=h).status_code == 404
    assert client.post("/api/bookings", json={"etd": "2026-12-10", "eta": "2026-12-01"}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"data": {"Bad Key": 1}}, headers=h).status_code == 422
    assert client.post("/api/bookings", json={"data": {"nested": {"a": {"b": 1}}}}, headers=h).status_code == 422
    upd = client.patch(f"/api/bookings/{b['id']}", json={"carrier": "ONE", "etd": None, "data": {"pcs": "5"}}, headers=h)
    assert upd.status_code == 200 and upd.json()["carrier"] == "ONE" and upd.json()["etd"] is None and upd.json()["data"] == {"pcs": "5"}


def test_release_numbers_the_booking_and_creates_the_freight_job_automatically(h):
    b = draft(h)
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert done.status_code == 200, done.text
    body = done.json()
    fy = fiscal_year(date.today())
    assert body["status"] == "CONFIRMED" and body["number"].startswith(f"BKG/{fy}/") and body["job_number"].startswith("SEAEXP-") and body["created_jobs"] == [body["job_number"]]
    job = next(j for j in client.get("/api/shipments", headers=h).json() if j["job_number"] == body["job_number"])
    assert job["mode"] == "Sea" and job["pol"] == "INMAA" and job["pod"] == "BEANR" and job["container_mode"] == "FCL" and job["consignee"] == "Solvay SA" and job["packages"] == 120 and job["job_type"] == "Forwarding"
    assert [j["job_type"] for j in client.get(f"/api/bookings/{b['id']}/jobs", headers=h).json()] == ["Forwarding"]
    assert client.patch(f"/api/bookings/{b['id']}", json={"carrier": "x"}, headers=h).status_code == 409
    assert client.delete(f"/api/bookings/{b['id']}", headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/confirm", headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Freight"}, headers=h).status_code == 409  # already created


def test_freight_plus_cha_release_creates_both_jobs_under_one_booking_number(h):
    b = draft(h, data={"pcs": "120", "gwt": "24000", "consignee": "Solvay SA", "marks": "Soda ash", "services": "Freight, CHA",
                       "charges": [{"type": "AR", "description": "Ocean Freight", "qty": "2", "rate": "850", "currency": "USD", "exch": "83.5", "service": "Freight"},
                                   {"type": "AP", "description": "Ocean Freight", "qty": "2", "rate": "700", "currency": "USD", "exch": "83.5", "service": "Freight", "vendor": "MSC Mediterranean"},
                                   {"type": "AR", "description": "Customs Clearance", "qty": "1", "rate": "4500", "currency": "INR", "exch": "1", "tax_pct": "18", "service": "CHA"},
                                   {"type": "AP", "description": "Customs Clearance", "qty": "1", "rate": "3000", "currency": "INR", "exch": "1", "service": "CHA", "vendor": "Sea Air Marine Surveyors"}]})
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Freight"}, headers=h).status_code == 409  # not confirmed yet
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
    assert done["number"].startswith("BKG/") and len(done["created_jobs"]) == 2 and {j["job_type"] for j in done["jobs"]} == {"Forwarding", "Customs"}
    fjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    cjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    fc = client.get(f"/api/shipments/by-number/{fjob}/charges", headers=h).json()["lines"]
    assert [c["charge_name"] for c in fc] == ["Ocean Freight"] and float(fc[0]["sell"]["rate"]) == 850 and fc[0]["buy"]["party"] == "MSC Mediterranean"
    cc = client.get(f"/api/shipments/by-number/{cjob}/charges", headers=h).json()["lines"]
    assert [c["charge_name"] for c in cc] == ["Customs Clearance"] and float(cc[0]["sell"]["tax_pct"]) == 18 and cc[0]["buy"]["party"] == "Sea Air Marine Surveyors"
    shipments = {j["job_number"]: j for j in client.get("/api/shipments", headers=h).json()}
    assert shipments[cjob]["pol"] == "INMAA" and shipments[cjob]["packages"] == 120 and shipments[cjob]["consignee"] == "Solvay SA" and shipments[cjob]["booking_id"] == shipments[fjob]["booking_id"]
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Freight"}, headers=h).status_code == 409
    assert client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Transport"}, headers=h).status_code == 422  # not booked
    cost = client.get(f"/api/bookings/{b['id']}/costing", headers=h).json()
    assert set(cost["services"]) == {"Freight", "CHA"} and float(cost["services"]["Freight"]["sell"]) == 850 * 2 * 83.5 and float(cost["services"]["CHA"]["sell"]) == 4500 and float(cost["services"]["CHA"]["buy"]) == 3000
    assert float(cost["combined"]["sell"]) == 850 * 2 * 83.5 + 4500 and float(cost["combined"]["buy"]) == 700 * 2 * 83.5 + 3000 and len(cost["lines"]) == 2


def test_customs_bills_are_kept_per_booking_and_show_in_the_freight_job(h):
    b = draft(h, data={"pcs": "10", "services": "Freight, CHA", "consignee": "X"})
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
    fjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    cjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    first = client.post(f"/api/customs/by-job/{cjob}", json={"bill_no": "SB7001", "bill_date": "2026-10-10", "reference": "CHA-REF-1", "assessable_value": "500000", "duty_amount": "0"}, headers=h)
    assert first.status_code == 201, first.text
    assert first.json()["bill_type"] == "SB" and first.json()["label"] == "Shipping Bill"
    assert client.post(f"/api/customs/by-job/{cjob}", json={"bill_no": "SB7001"}, headers=h).status_code == 409  # a number is recorded once
    second = client.post(f"/api/customs/by-job/{cjob}", json={"bill_no": "SB7002", "bill_date": "2026-10-11", "reference": "CHA-REF-2"}, headers=h)
    assert second.status_code == 201 and second.json()["count"] == 2
    fj = client.get(f"/api/customs/by-job/{fjob}", headers=h).json()
    assert fj["count"] == 2 and [x["bill_no"] for x in fj["bills"]] == ["SB7001", "SB7002"] and fj["primary_no"] == "SB7001" and str(fj["primary_date"]) == "2026-10-10" and fj["booking_number"] == done["number"]
    assert all(x["entered_in_job"] == cjob for x in fj["bills"])
    bid = fj["bills"][0]["id"]
    assert client.patch(f"/api/customs/{bid}", json={"status": "Cleared", "ooc_date": "2026-10-12"}, headers=h).status_code == 200
    assert client.patch(f"/api/customs/{fj['bills'][1]['id']}", json={"status": "Cancelled"}, headers=h).json()["count"] == 1
    assert len(client.get(f"/api/customs/by-job/{fjob}", headers=h).json()["bills"]) == 2  # the cancelled record is still listed
    rd = client.get(f"/api/jobs/{fjob}/report-data", headers=h).json()
    assert rd["booking_number"] == done["number"] and rd["shipping_bills"][0]["bill_no"] == "SB7001" and rd["bills_of_entry"] == [] and rd["bl_awb"]["label"] == "BL"
    imp = client.post("/api/bookings", json={"mode": "Sea", "direction": "Import", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Acme", "carrier": "MSC", "pol": "DEHAM", "pod": "INMAA", "data": {"services": "Freight", "pcs": "5"}}, headers=h).json()
    ij = client.post(f"/api/bookings/{imp['id']}/confirm", headers=h).json()["job_number"]
    be = client.post(f"/api/customs/by-job/{ij}", json={"bill_no": "BE9001", "bill_date": "2026-10-14"}, headers=h).json()
    assert be["bill_type"] == "BE" and be["label"] == "Bill of Entry"
    assert client.get(f"/api/jobs/{ij}/report-data", headers=h).json()["bills_of_entry"][0]["bill_no"] == "BE9001"


def test_confirm_requires_the_key_fields(h):
    empty = client.post("/api/bookings", json={}, headers=h).json()
    r = client.post(f"/api/bookings/{empty['id']}/confirm", headers=h)
    assert r.status_code == 422 and "carrier" in r.json()["detail"] and "port of loading" in r.json()["detail"]


def test_numbers_are_gap_free_even_under_concurrency(h):
    ids = [draft(h)["id"] for _ in range(6)]
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(TestClient(app).post(f"/api/bookings/{i}/confirm", headers=h).json()["number"])) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 6
    seq = sorted(int(n.rsplit("/", 1)[1]) for n in out)
    assert seq == list(range(seq[0], seq[0] + 6))


def test_cancel_release_space_and_summary(h):
    a, b = draft(h), draft(h, si_cutoff=str(date.today() + timedelta(days=1)))
    client.post(f"/api/bookings/{a['id']}/confirm", headers=h)
    client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert client.post(f"/api/bookings/{a['id']}/cancel", headers=h).json()["status"] == "CANCELLED"
    assert client.post(f"/api/bookings/{b['id']}/release-space", headers=h).json()["status"] == "SPACE_RELEASED"
    assert client.post(f"/api/bookings/{a['id']}/cancel", headers=h).status_code == 409
    assert client.post(f"/api/bookings/{draft(h)['id']}/cancel", headers=h).status_code == 409
    s = client.get("/api/bookings/summary", headers=h).json()
    assert s["space_released"] >= 1 and s["confirmed_this_week"] >= 1 and s["si_cutoff_48h"] >= 1 and s["vgm_pending"] >= 1
    assert [x["id"] for x in client.get("/api/bookings?status=SPACE_RELEASED", headers=h).json()][0] == b["id"] or b["id"] in [x["id"] for x in client.get("/api/bookings?status=SPACE_RELEASED", headers=h).json()]


def test_duplicate_creates_a_clean_draft(h):
    src = draft(h)
    client.post(f"/api/bookings/{src['id']}/confirm", headers=h)
    copy = client.post(f"/api/bookings/{src['id']}/duplicate", headers=h)
    assert copy.status_code == 201
    c = copy.json()
    assert c["status"] == "DRAFT" and c["number"].startswith("BKG/") and c["number"] != src["number"] and c["job_number"] is None and c["carrier_booking_no"] is None and c["pol"] == "INMAA" and c["data"]["consignee"] == "Solvay SA"


def test_draft_delete_and_search(h):
    d = draft(h, party_name="Zebra Searchable Co")
    assert [x["id"] for x in client.get("/api/bookings?q=zebra", headers=h).json()] == [d["id"]]
    assert client.delete(f"/api/bookings/{d['id']}", headers=h).status_code == 204
    assert client.get(f"/api/bookings/{d['id']}", headers=h).status_code == 404


def test_tenant_isolation_and_permissions(world, admin, h):
    b = draft(h)
    assert client.get(f"/api/bookings/{b['id']}", headers=hdr(world["demo"], portal="demo")).status_code in (403, 404)
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER", "OPERATIONS"):
        client.post("/api/users", json={"username": f"{role[:2].lower()}{tag}", "email": f"{role[:2].lower()}{tag}@b.test", "password": PASSWORD, "first_name": role, "role": role}, headers=h)
    viewer, ops = hdr(f"vi{tag}", admin["license"]), hdr(f"op{tag}", admin["license"])
    assert client.get(f"/api/bookings/{b['id']}", headers=viewer).status_code == 200
    assert client.post("/api/bookings", json={}, headers=viewer).status_code == 403
    assert client.post(f"/api/bookings/{b['id']}/confirm", headers=viewer).status_code == 403
    assert client.post("/api/bookings", json={}, headers=ops).status_code == 201
    assert client.get("/api/bookings").status_code == 401
    assert client.get("/api/bookings", headers=hdr(world["dev"], portal="developer")).status_code == 403


@pytest.fixture(scope="module")
def h2(world, admin2):
    return hdr(admin2["username"], admin2["license"])


def _accepted_quote(h, h2):
    line = lambda name, rate, side="SELL": {"side": side, "charge_name": name, "carrier": "", "basis": "Per Container", "ccy": "USD", "exch": "83.5", "rate": rate, "qty": "2", "tax_pct": "0"}
    q = client.post("/api/quotations", json={"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "BEANR", "lines": [line("Ocean Freight", "850"), line("Ocean Freight", "700", "BUY")]}, headers=h).json()
    assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).status_code == 200
    assert client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2).status_code == 200
    assert client.post(f"/api/quotations/{q['id']}/send", headers=h).status_code == 200
    assert client.post(f"/api/quotations/{q['id']}/accept", headers=h).status_code == 200
    return q["id"]


def test_booking_raised_from_a_quotation_converts_it_when_the_job_is_created_at_release(h, h2):
    qid = _accepted_quote(h, h2)
    b = draft(h, quotation_id=qid, data={"pcs": "120", "gwt": "24000", "consignee": "Solvay SA"})
    assert b["quotation_id"] == qid
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert done.status_code == 200, done.text
    job_no = done.json()["job_number"]  # the freight job is created at release
    quote = client.get(f"/api/quotations/{qid}", headers=h).json()
    assert quote["status"] == "CONVERTED" and quote["job_number"] == job_no
    charges = client.get(f"/api/shipments/by-number/{job_no}/charges", headers=h).json()["lines"]
    assert any(c["charge_name"] == "Ocean Freight" and float(c["sell"]["rate"]) == 850 and float(c["buy"]["rate"]) == 700 for c in charges)  # no costing rows on the booking: the quotation's charges are used


def test_a_quotation_that_is_not_accepted_cannot_be_booked(h, h2):
    line = {"side": "SELL", "charge_name": "Ocean Freight", "carrier": "", "basis": "Per Container", "ccy": "USD", "exch": "83.5", "rate": "850", "qty": "2", "tax_pct": "0"}
    q = client.post("/api/quotations", json={"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "BEANR", "lines": [line]}, headers=h).json()
    b = draft(h, quotation_id=q["id"])
    r = client.post(f"/api/bookings/{b['id']}/confirm", headers=h)
    assert r.status_code == 409 and "accepted" in r.json()["detail"]
    assert client.get(f"/api/bookings/{b['id']}", headers=h).json()["status"] == "DRAFT"  # nothing was half-created
    assert client.post("/api/bookings", json={"party_name": "X", "quotation_id": 99999999}, headers=h).status_code == 404


def test_container_wise_rows_become_one_head_with_hsn_from_the_charge_master(h):
    code = "OFC" + uuid.uuid4().hex[:4].upper()
    name = "Ocean Freight Charges " + code
    made = client.post("/api/masters/charge", json={"code": code, "name": name, "data": {"hsn_code": "996521", "gst_percent": "5", "ar_ap": "Both", "category": "Freight"}}, headers=h)
    assert made.status_code in (200, 201), made.text
    b = draft(h, data={"pcs": "1", "consignee": "X", "services": "Freight",
                       "charges": [{"type": "AR", "description": name + " — 20GP", "qty": "1", "rate": "2500", "currency": "USD", "exch": "88", "tax_pct": "5", "service": "Freight"},
                                   {"type": "AR", "description": name + " — 40HC", "qty": "1", "rate": "3200", "currency": "USD", "exch": "88", "tax_pct": "5", "service": "Freight"},
                                   {"type": "AP", "description": name + " — 20GP", "qty": "1", "rate": "2200", "currency": "USD", "exch": "88", "service": "Freight", "vendor": "MSC"},
                                   {"type": "AP", "description": name + " — 40HC", "qty": "1", "rate": "1200", "currency": "USD", "exch": "88", "service": "Freight", "vendor": "MSC"}]})
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
    jn = done["created_jobs"][0]
    lines = client.get(f"/api/shipments/by-number/{jn}/charges", headers=h).json()["lines"]
    assert len(lines) == 1, lines
    ln = lines[0]
    assert ln["charge_name"] == name and ln["hsn"] == "996521"
    assert float(ln["sell"]["rate"]) == 5700 and float(ln["buy"]["rate"]) == 3400 and float(ln["qty"]) == 1
    assert {b["type"]: (b["sell"], b["buy"]) for b in ln["breakup"]} == {"20GP": ("2500", "2200"), "40HC": ("3200", "1200")}
    doc = client.get("/api/documents/billing/billable-jobs", params={"party": "Acme Exports", "doc_type": "invoice"}, headers=h)
    assert doc.status_code == 200
    unbilled = client.get("/api/documents/billing/job-lines", params={"job_number": jn, "party": "Acme Exports", "doc_type": "invoice"}, headers=h).json()
    assert len(unbilled) == 1 and "20GP x1 @ 2500" in unbilled[0]["description"] and "40HC x1 @ 3200" in unbilled[0]["description"] and "2200" not in unbilled[0]["description"]
    bills = client.get("/api/documents/billing/job-lines", params={"job_number": jn, "party": "MSC", "doc_type": "purchase_invoice"}, headers=h).json()
    assert len(bills) == 1 and "20GP x1 @ 2200" in bills[0]["description"] and "40HC x1 @ 1200" in bills[0]["description"]


def test_duplicate_job_booking_and_quotation_start_as_fresh_drafts(h):
    b = draft(h)
    conf = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
    jn = conf["job_number"]
    client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": [{"charge_name": "Ocean Freight", "qty": "1", "hsn": "996521", "sell": {"ccy": "INR", "rate": "100", "tax_type": "Taxable", "tax_pct": "18", "party": "Acme"}, "buy": {"ccy": "INR", "rate": "60", "tax_type": "Taxable", "tax_pct": "18", "party": "MSC"}}]}, headers=h)
    d = client.post(f"/api/shipments/by-number/{jn}/duplicate", headers=h)
    assert d.status_code == 201, d.text
    new = d.json()
    assert new["job_number"] != jn and new["job_number"].startswith("SEAEXP-") and new["job_stage"] == "Draft" and new["booking_id"] is None and new["pol"] == "INMAA"
    lines = client.get(f"/api/shipments/by-number/{new['job_number']}/charges", headers=h).json()["lines"]
    assert len(lines) == 1 and lines[0]["hsn"] == "996521"
    bd = client.post(f"/api/bookings/{b['id']}/duplicate", headers=h).json()
    assert bd["status"] == "DRAFT" and bd["number"] and bd["number"] != b["number"] and bd["id"] != b["id"]
    q = client.post("/api/quotations", json={"party_name": "Dup Co", "mode": "Sea", "lines": [{"side": "SELL", "charge_name": "Ocean Freight", "ccy": "INR", "exch": "1", "rate": "100", "qty": "1", "basis": "Per Shipment"}]}, headers=h).json()
    qd = client.post(f"/api/quotations/{q['id']}/duplicate", headers=h)
    assert qd.status_code == 201, qd.text
    assert qd.json()["status"] == "DRAFT" and qd.json()["id"] != q["id"] and qd.json()["number"] and qd.json()["number"] != q["number"] and len(qd.json()["lines"]) == 1 and qd.json()["revision_no"] == 0


def test_job_cost_container_wise_breakup_is_totalled_by_the_server_and_reaches_the_invoice(h):
    b = draft(h)
    jn = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()["job_number"]
    side = lambda rate: {"ccy": "INR", "exch": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0", "party": "", "use_party": False}
    line = {"charge_name": "Terminal Handling", "qty": "5", "uom": "Per Container", "hsn": "996719", "sell": {**side("0"), "party": "Acme Exports", "use_party": True}, "buy": {**side("0"), "party": "CFS One", "use_party": True},
            "breakup": [{"type": "20GP", "qty": "2", "sell": "15000", "buy": "12000"}, {"type": "40HC", "qty": "1", "sell": "19000", "buy": "15000"}]}
    r = client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": [line]}, headers=h)
    assert r.status_code == 200, r.text
    saved = r.json()["lines"][0]
    assert float(saved["qty"]) == 1 and float(saved["sell"]["rate"]) == 49000 and float(saved["buy"]["rate"]) == 39000 and len(saved["breakup"]) == 2
    inv = client.get("/api/documents/billing/job-lines", params={"job_number": jn, "party": "Acme Exports", "doc_type": "invoice"}, headers=h).json()
    assert inv[0]["description"].endswith("(20GP x2 @ 15000; 40HC x1 @ 19000)") or "20GP x2 @ 15000; 40HC x1 @ 19000" in inv[0]["description"]
    pur = client.get("/api/documents/billing/job-lines", params={"job_number": jn, "party": "CFS One", "doc_type": "purchase_invoice"}, headers=h).json()
    assert "20GP x2 @ 12000; 40HC x1 @ 15000" in pur[0]["description"]
