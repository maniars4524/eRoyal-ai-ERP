from datetime import date, timedelta

import pytest

from tests.test_bookings import client, draft, hdr  # noqa: F401  (same login helpers and fixtures)
from tests.helpers import confirm_with_jobs  # noqa: E402


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def released(h, **data):
    b = draft(h, data={"pcs": "10", "services": "Freight, CHA", "consignee": "Solvay SA", **data})
    done = confirm_with_jobs(client, h, b['id']).json()
    fjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    cjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    return b, done, fjob, cjob


def test_cha_job_of_a_booking_points_at_its_freight_job_and_starts_at_created(h):
    b, done, fjob, cjob = released(h)
    view = client.get(f"/api/jobs/{fjob}/cha-jobs", headers=h).json()
    assert view["freight_job_number"] == fjob and view["booking_number"] == done["number"] and view["count"] == 1
    row = view["cha_jobs"][0]
    assert row["cha_job_number"] == cjob and row["customs_status"] == "CREATED" and row["linked_by"] == "parent" and row["shipment_type"] == "Export" and row["shipment_mode"] == "Sea"
    assert [d["key"] for d in row["documents"]] == ["bl"]
    # seen from the CHA job it is the same freight job
    assert client.get(f"/api/jobs/{cjob}/cha-jobs", headers=h).json()["freight_job_number"] == fjob


def test_additional_cha_job_needs_matching_parties_and_ports_unless_an_exception_is_given(h):
    b, done, fjob, cjob = released(h)
    same = client.post(f"/api/jobs/{fjob}/cha-jobs", json={}, headers=h)
    assert same.status_code == 201, same.text
    assert same.json()["count"] == 2 and same.json()["created"] != cjob
    bad = client.post(f"/api/jobs/{fjob}/cha-jobs", json={"importer": "Someone Else GmbH", "pod": "DEHAM"}, headers=h)
    assert bad.status_code == 409
    d = bad.json()["detail"]
    assert d["code"] == "CHA_MISMATCH" and len(d["mismatches"]) == 2
    ok = client.post(f"/api/jobs/{fjob}/cha-jobs", json={"importer": "Someone Else GmbH", "pod": "DEHAM", "exception_reason": "Split consignment"}, headers=h)
    assert ok.status_code == 201 and ok.json()["count"] == 3
    # still one freight job and one booking number
    jobs = client.get(f"/api/bookings/{b['id']}/jobs", headers=h).json()
    assert [j["job_type"] for j in jobs].count("Forwarding") == 1 and [j["job_type"] for j in jobs].count("Customs") == 3


def test_each_cha_job_keeps_its_own_bills_and_stage(h):
    b, done, fjob, cjob = released(h)
    second = client.post(f"/api/jobs/{fjob}/cha-jobs", json={}, headers=h).json()["created"]
    assert client.post(f"/api/customs/by-job/{cjob}", json={"bill_no": "SBCHA01", "bill_date": "2026-10-10"}, headers=h).status_code == 201
    assert client.post(f"/api/customs/by-job/{second}", json={"bill_no": "SBCHA02", "bill_date": "2026-10-11"}, headers=h).status_code == 201
    rows = {r["cha_job_number"]: r for r in client.get(f"/api/jobs/{fjob}/cha-jobs", headers=h).json()["cha_jobs"]}
    assert rows[cjob]["shipping_bill_no"] == "SBCHA01" and rows[second]["shipping_bill_no"] == "SBCHA02" and rows[cjob]["bill_of_entry_no"] is None
    mv = client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "CUSTOMS FILED"}, headers=h)
    assert mv.status_code == 200
    rows = {r["cha_job_number"]: r for r in client.get(f"/api/jobs/{fjob}/cha-jobs", headers=h).json()["cha_jobs"]}
    assert rows[cjob]["customs_status"] == "CUSTOMS FILED" and rows[second]["customs_status"] == "CREATED"
    assert client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "DOCUMENTATION"}, headers=h).status_code == 422       # back needs a reason
    assert client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "DOCUMENTATION", "remarks": "wrong HS code"}, headers=h).status_code == 200
    assert client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "CLOSED"}, headers=h).status_code == 409
    assert client.patch(f"/api/cha/jobs/{cjob}/status", json={"status": "NOPE"}, headers=h).status_code == 422
    assert client.patch(f"/api/cha/jobs/{fjob}/status", json={"status": "VALIDATED"}, headers=h).status_code == 422             # a freight job has no customs stage


def test_create_standalone_cha_job_register_dashboard_and_detail(h):
    before = client.get("/api/cha/dashboard", headers=h).json()
    new = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Air", "exporter": "Berlin GmbH", "importer": "Chennai Traders", "pol": "DEFRA", "pod": "INMAA", "eta": str(date.today() - timedelta(days=3)),
                                              "hbl": "HAWB77", "file_ref": "F-1", "customs_location": "INMAA4"}, headers=h)
    assert new.status_code == 201, new.text
    job = new.json()
    assert job["shipment_type"] == "Import" and job["shipment_mode"] == "Air" and job["customs_status"] == "CREATED" and job["freight_job_number"] is None and job["info"]["hbl"] == "HAWB77"
    assert job["exporter"] == "Berlin GmbH" and job["importer"] == "Chennai Traders" and [d["key"] for d in job["documents"]] == ["can", "do"] and job["delayed"] is True
    after = client.get("/api/cha/dashboard", headers=h).json()
    assert after["total"] == before["total"] + 1 and after["groups"]["draft"] == before["groups"]["draft"] + 1 and after["groups"]["delayed"] == before["groups"]["delayed"] + 1
    assert after["matrix"]["Air Import"]["active"] == before["matrix"]["Air Import"]["active"] + 1
    lst = client.get("/api/cha/jobs", params={"direction": "Import", "mode": "Air", "group": "delayed"}, headers=h).json()
    assert job["cha_job_number"] in [r["cha_job_number"] for r in lst["items"]]
    assert job["cha_job_number"] not in [r["cha_job_number"] for r in client.get("/api/cha/jobs", params={"direction": "Export"}, headers=h).json()["items"]]
    assert job["cha_job_number"] in [r["cha_job_number"] for r in client.get("/api/cha/jobs", params={"q": "HAWB77"}, headers=h).json()["items"]]
    for st in ("VALIDATED", "CUSTOMS FILED", "OOC / LEO CLEARED", "DELIVERED"):
        assert client.patch(f"/api/cha/jobs/{job['cha_job_number']}/status", json={"status": st}, headers=h).status_code == 200
    done = client.get("/api/cha/dashboard", headers=h).json()
    assert done["groups"]["completed"] == before["groups"]["completed"] + 1 and done["groups"]["delayed"] == before["groups"]["delayed"]
    upd = client.patch(f"/api/cha/jobs/{job['cha_job_number']}", json={"file_ref": "F-2"}, headers=h)
    assert upd.status_code == 200 and upd.json()["info"]["file_ref"] == "F-2" and any(e["type"] == "CHA_STAGE_CHANGED" for e in upd.json()["events"])


def test_standalone_cha_job_can_link_to_a_freight_job_only_when_it_matches(h):
    b, done, fjob, cjob = released(h)
    wrong = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Sea", "freight_job_number": fjob}, headers=h)
    assert wrong.status_code == 409 and "cannot be linked" in wrong.json()["detail"]
    ok = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "freight_job_number": fjob}, headers=h)
    assert ok.status_code == 201 and ok.json()["freight_job_number"] == fjob and len(ok.json()["siblings"]) == 1
    assert client.get(f"/api/cha/jobs/{fjob}", headers=h).status_code == 422
    assert client.post("/api/cha/jobs", json={"job_type": "Sideways", "mode": "Sea"}, headers=h).status_code == 422


def booking(h, services, direction="Export", mode="Sea", **extra):
    b = draft(h, direction=direction, mode=mode, data={"pcs": "10", "services": services, "consignee": "Solvay SA", "commodity": "Soda ash"}, **extra)
    done = confirm_with_jobs(client, h, b['id']).json()
    return done


def test_cha_bookings_list_offers_only_confirmed_bookings_with_the_cha_service(h):
    cha_only = booking(h, "CHA")
    combo = booking(h, "Freight, CHA")
    cha_trp = booking(h, "CHA, Transport")
    plain = booking(h, "Freight")
    unconfirmed = draft(h, data={"services": "CHA"})
    nums = {x["number"]: x for x in client.get("/api/cha/bookings", headers=h).json()["items"]}
    assert cha_only["number"] in nums and combo["number"] in nums and cha_trp["number"] in nums
    assert plain["number"] not in nums and unconfirmed["number"] not in nums
    row = nums[combo["number"]]
    assert row["combo"] == "Freight + CHA" and row["exporter"] == "Acme Exports" and row["importer"] == "Solvay SA" and row["pol"] == "INMAA" and row["pod"] == "BEANR" and row["freight_job_number"] and len(row["cha_jobs"]) == 1
    assert nums[cha_only["number"]]["freight_job_number"] is None
    assert all(x["direction"] == "Import" for x in client.get("/api/cha/bookings", params={"direction": "Import"}, headers=h).json()["items"])


def test_cha_job_from_a_booking_carries_the_booking_and_a_second_one_shares_the_number(h):
    done = booking(h, "Freight, CHA")
    cjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    first = client.get(f"/api/cha/jobs/{cjob}", headers=h).json()
    assert first["booking_number"] == done["number"] and first["source_info"]["booking_number"] == done["number"] and first["source_info"]["order"] == "CHA first, then freight"
    assert first["source_info"]["booking_source"] == "Booking" and first["exporter"] == "Acme Exports" and first["importer"] == "Solvay SA"
    again = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": done["number"]}, headers=h)
    assert again.status_code == 201, again.text
    j = again.json()
    assert j["booking_number"] == done["number"] and j["cha_job_number"] != cjob and j["freight_job_number"] == first["freight_job_number"] and j["exporter"] == "Acme Exports" and j["pol"] == "INMAA"
    assert client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Sea", "booking_number": done["number"]}, headers=h).status_code == 409
    assert client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": "BKG/NOPE/1"}, headers=h).status_code == 404
    only_freight = booking(h, "Freight")
    assert client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": only_freight["number"]}, headers=h).status_code == 422
    view = client.get(f"/api/jobs/{first['freight_job_number']}/cha-jobs", headers=h).json()
    assert view["count"] == 2 and view["workflow"]["order"] == "CHA first, then freight" and view["workflow"]["cha_cleared"] is False


def test_cha_only_booking_takes_more_cha_jobs_without_a_freight_job(h):
    done = booking(h, "CHA")
    assert [j["job_type"] for j in done["jobs"]] == ["Customs"]
    more = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": done["number"]}, headers=h)
    assert more.status_code == 201 and more.json()["booking_number"] == done["number"] and more.json()["freight_job_number"] is None
    jobs = client.get(f"/api/bookings/{done['id']}/jobs", headers=h).json()
    assert [j["job_type"] for j in jobs] == ["Customs", "Customs"]


def test_import_workflow_is_freight_first(h):
    done = booking(h, "Freight, CHA", direction="Import", mode="Sea")
    fjob = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    wf = client.get(f"/api/jobs/{fjob}/cha-jobs", headers=h).json()["workflow"]
    assert wf["order"] == "Freight first, then CHA"


def test_direct_jobs_get_a_system_generated_booking_number(h):
    new = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Air", "exporter": "Direct Exports", "importer": "Buyer", "pol": "INMAA", "pod": "DEFRA"}, headers=h)
    assert new.status_code == 201, new.text
    job = new.json()
    assert job["booking_number"] and job["booking_number"].startswith("BKG/") and job["source_info"]["booking_source"] == "System generated (direct job)"
    bk = next(x for x in client.get("/api/bookings", headers=h).json() if x["number"] == job["booking_number"])
    assert bk["status"] == "CONFIRMED" and bk["data"]["auto_generated"] is True and bk["data"]["services"] == "CHA"
    src = client.get(f"/api/jobs/{job['cha_job_number']}/source", headers=h).json()
    assert src["booking"]["number"] == job["booking_number"] and src["auto_booking"] is True
    # a direct freight job too
    fj = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "BEANR"}, headers=h)
    assert fj.status_code == 201, fj.text
    fsrc = client.get(f"/api/jobs/{fj.json()['job_number']}/source", headers=h).json()
    assert fsrc["booking"]["number"].startswith("BKG/") and fsrc["auto_booking"] is True
    assert fsrc["booking"]["number"] != job["booking_number"]


def test_confirming_a_booking_only_numbers_it_and_the_jobs_are_made_from_the_booking_number(h):
    b = draft(h, data={"pcs": "10", "services": "Freight, CHA", "consignee": "Solvay SA"})
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
    assert done["number"].startswith("BKG/") and done["created_jobs"] == [] and done["jobs"] == [] and done["job_number"] is None
    # both modules offer the booking
    assert done["number"] in [x["number"] for x in client.get("/api/bookings/open-for/Freight", headers=h).json()["items"]]
    assert done["number"] in [x["number"] for x in client.get("/api/bookings/open-for/CHA", headers=h).json()["items"]]
    assert client.get("/api/bookings/open-for/Teleport", headers=h).status_code == 422
    # the CHA job is made in the CHA module: its own number series, the booking number is shared
    cha = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": done["number"]}, headers=h)
    assert cha.status_code == 201, cha.text
    cj = cha.json()
    assert cj["cha_job_number"].startswith("SE/CC/") and cj["booking_number"] == done["number"] and cj["freight_job_number"] is None
    # the freight job is made from the same booking number in the Freight module
    fr = client.post(f"/api/bookings/{b['id']}/jobs", json={"service": "Freight"}, headers=h)
    assert fr.status_code == 201 and fr.json()["created_job"].startswith("SEAEXP-")
    assert done["number"] not in [x["number"] for x in client.get("/api/bookings/open-for/Freight", headers=h).json()["items"]]
    view = client.get(f"/api/jobs/{fr.json()['created_job']}/cha-jobs", headers=h).json()
    assert view["count"] == 1 and view["cha_jobs"][0]["cha_job_number"] == cj["cha_job_number"] and view["booking_number"] == done["number"]
    # a second CHA job of the same booking: the next number of the same series
    again = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "booking_number": done["number"]}, headers=h).json()
    n1, n2 = int(cj["cha_job_number"].rsplit("/", 1)[1]), int(again["cha_job_number"].rsplit("/", 1)[1])
    assert again["cha_job_number"].startswith("SE/CC/") and n2 == n1 + 1


def test_cha_numbers_are_per_shipment_type(h):
    seen = {}
    for mode, direction, code in (("Sea", "Export", "SE"), ("Sea", "Import", "SI"), ("Air", "Export", "AE"), ("Air", "Import", "AI")):
        r = client.post("/api/cha/jobs", json={"job_type": direction, "mode": mode, "exporter": "Num Test", "importer": "Num Buyer"}, headers=h)
        assert r.status_code == 201, r.text
        no = r.json()["cha_job_number"]
        assert no.startswith(f"{code}/CC/") and len(no.rsplit("/", 1)[1]) >= 3
        seen[code] = no
    assert len(set(seen.values())) == 4


def test_filing_form_defaults_validation_and_saving(h):
    imp = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Sea", "exporter": "Overseas Co", "importer": "Local Importer", "pol": "SGSIN", "pod": "INMAA"}, headers=h).json()
    no = imp["cha_job_number"]
    assert imp["filing_label"] == "Bill of Entry Filing" and imp["filing_tabs"] == ["General", "IGM", "Invoice", "Product"]
    keys = {f["k"] for sec in imp["filing_form"] for f in sec["fields"]}
    assert {"importer_name", "iec", "ad_code", "be_type", "mode_of_transport", "high_sea_sale", "commercial_tax_type"} <= keys
    assert imp["filing_defaults"]["importer_name"] == "Local Importer" and imp["filing_defaults"]["mode_of_transport"] == "S" and imp["filing_defaults"]["be_type"] == "H" and imp["filing_defaults"]["port_origin"] == "SGSIN"
    v = client.get(f"/api/cha/jobs/{no}/validate", headers=h).json()
    assert v["ready"] is False and v["checked"] >= 10 and v["passed"] < v["checked"] and any(e["key"] == "iec" for e in v["errors"])
    form = {"iec": "ABCDE1234H", "branch_sr_no": "0 - CHENNAI", "addr1": "Address 1", "ad_code": "1234567", "country_origin": "SG - SINGAPORE", "country_consignment": "SG - SINGAPORE", "commercial_tax_type": "G"}
    saved = client.patch(f"/api/cha/jobs/{no}", json={"filing": form}, headers=h)
    assert saved.status_code == 200 and saved.json()["filing"]["iec"] == "ABCDE1234H"
    v2 = client.get(f"/api/cha/jobs/{no}/validate", headers=h).json()
    assert v2["ready"] is True and v2["errors"] == [] and v2["passed"] == v2["checked"], v2
    assert client.patch(f"/api/cha/jobs/{no}", json={"filing": {"nonsense": "x"}}, headers=h).status_code == 422
    exp = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "Madras Exports", "importer": "Buyer", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    assert exp["filing_label"] == "Shipping Bill Filing" and exp["filing_tabs"] == ["General", "Invoice", "Item", "Annexure C1"]
    assert {"gstn_type", "gstn_id", "exporter_class", "consignee"} <= {f["k"] for sec in exp["filing_form"] for f in sec["fields"]} and exp["filing_defaults"]["port_loading"] == "INMAA"


def test_cha_jobs_are_not_created_through_the_shipment_job_form(h):
    r = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "job_type": "Customs"}, headers=h)
    assert r.status_code == 422 and "CHA module" in r.json()["detail"]


def test_invoice_job_picker_can_be_limited_to_freight_cha_or_transport(h):
    done = booking(h, "Freight, CHA")
    fj = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    cj = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    party = "Category Customer Ltd"
    for jn, name in ((fj, "Ocean Freight"), (cj, "Customs Clearance")):
        r = client.put(f"/api/shipments/by-number/{jn}/charges", json={"lines": [{"charge_name": name, "uom": "Per Shipment", "qty": "1", "sell": {"ccy": "INR", "exch": "1", "rate": "1000", "tax_type": "Taxable", "tax_pct": "18", "party": party}, "buy": {}}]}, headers=h)
        assert r.status_code == 200, r.text
    q = lambda svc: {x["job_number"]: x for x in client.get("/api/documents/billing/billable-jobs", params={"party": party, "doc_type": "invoice", "service": svc}, headers=h).json()}
    allj, fr, ch, tr = q(""), q("Freight"), q("CHA"), q("Transport")
    assert fj in allj and cj in allj and allj[cj]["job_type"] == "Customs"
    assert fj in fr and cj not in fr and cj in ch and fj not in ch and not tr
    assert client.get("/api/documents/billing/billable-jobs", params={"party": party, "service": "Bogus"}, headers=h).status_code == 422


def test_cha_job_documents_become_ready_when_the_bl_number_is_entered_on_the_cha_job(h):
    j = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "Doc Exports", "importer": "Doc Buyer", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    assert [d["available"] for d in j["documents"]] == [False]
    ok = client.patch(f"/api/cha/jobs/{j['cha_job_number']}", json={"hbl": "HBL12345"}, headers=h).json()
    assert ok["documents"][0]["available"] is True and ok["documents"][0]["number"] == "HBL12345"


def test_company_licence_modules_gate_the_cha_module_and_reach_the_client(h):
    from app.db import SessionLocal
    from app.models import Organization, User
    from sqlalchemy import select

    me = client.get("/api/auth/me", headers=h).json()
    assert "company_modules" in me and me["company_modules"] is None           # None = every module
    with SessionLocal() as db:
        org = db.get(Organization, me["organization_id"])
        saved = org.modules
        org.modules = ["accounts", "transport"]
        db.commit()
    try:
        assert client.get("/api/auth/me", headers=h).json()["company_modules"] == ["accounts", "transport"]
        denied = client.get("/api/cha/dashboard", headers=h)
        assert denied.status_code == 403 and "licence" in denied.json()["detail"]
    finally:
        with SessionLocal() as db:
            db.get(Organization, me["organization_id"]).modules = saved
            db.commit()
    assert client.get("/api/cha/dashboard", headers=h).status_code == 200


def test_invoice_job_picker_offers_a_job_without_charges_so_cost_and_selling_can_be_entered(h):
    done = booking(h, "CHA")
    cj = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    party = "Acme Exports"          # the bill-to party is the job's own exporter
    get = lambda **kw: {x["job_number"]: x for x in client.get("/api/documents/billing/billable-jobs", params={"party": party, "doc_type": "invoice", "service": "CHA", **kw}, headers=h).json()}
    assert cj not in get()
    assert cj not in {x["job_number"] for x in client.get("/api/documents/billing/billable-jobs", params={"party": "Somebody Else", "doc_type": "invoice", "service": "CHA", "include_empty": "true"}, headers=h).json()}
    row = get(include_empty="true")[cj]
    assert row["empty"] is True and row["lines"] == 0 and row["job_type"] == "Customs"
    r = client.put(f"/api/shipments/by-number/{cj}/charges", json={"lines": [{"charge_name": "Customs Clearance", "uom": "Per Shipment", "qty": "1", "buy": {"ccy": "INR", "exch": "1", "rate": "3000", "tax_type": "Non-Taxable", "tax_pct": "0", "party": "Broker A"}, "sell": {"ccy": "INR", "exch": "1", "rate": "4500", "tax_type": "Taxable", "tax_pct": "18", "party": party}}]}, headers=h)
    assert r.status_code == 200, r.text
    after = get(include_empty="true")[cj]
    assert not after.get("empty") and after["lines"] == 1 and float(after["amount"]) > 0
    lines = client.get("/api/documents/billing/job-lines", params={"job_number": cj, "party": party, "doc_type": "invoice"}, headers=h).json()
    assert [l["charge_name"] for l in lines] == ["Customs Clearance"] and float(lines[0]["rate"]) == 4500


def test_import_job_tabs_are_saved_validated_and_logged(h):
    imp = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Air", "exporter": "Sup Co", "importer": "Imp Co", "pol": "SGSIN", "pod": "INMAA"}, headers=h).json()
    no = imp["cha_job_number"]
    tabs = {t["tab"] for t in imp["spec"]}
    assert {"IGM", "Invoice", "Product", "e-Way Bill", "Job History"} <= tabs and "Annexure C1" not in tabs and imp["data"] == {}
    sections = {s["title"] for t in imp["spec"] if t["tab"] == "Product" for c in t["collections"] for s in c["sections"]}
    assert {"ADD", "Licence", "S/W"} <= sections
    exp = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "E", "importer": "I", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    assert {"Invoice", "Item", "Annexure C1", "e-Way Bill"} <= {t["tab"] for t in exp["spec"]} and "IGM" not in {t["tab"] for t in exp["spec"]}
    data = {"igm": [{"igm_no": "9854321", "igm_date": "2026-05-13", "mawb_no": "110897654321", "packages": "1", "gross_weight": "2.000"}],
            "invoices": [{"invoice_no": "7534451158", "invoice_date": "2025-07-10", "terms": "FOB", "currency": "USD", "invoice_value": "886526.03", "exchange_rate": "97.05", "supplier_name": "SUPPLIER"}],
            "items": [{"invoice_sr": "1", "description": "TEST", "qty": "24", "qty_unit": "KGS", "unit_price": "8865.26", "cth_no": "29199090", "levy_pct": "5.00"}]}
    ok = client.patch(f"/api/cha/jobs/{no}", json={"data": data}, headers=h)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["data"]["invoices"][0]["invoice_no"] == "7534451158" and body["data"]["items"][0]["cth_no"] == "29199090" and len(body["data"]["igm"]) == 1
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": {"items": [{"nonsense": "x"}]}}, headers=h).status_code == 422
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": {"mystery": []}}, headers=h).status_code == 422
    # every change goes to the job log with who and what
    edit = client.patch(f"/api/cha/jobs/{no}", json={"data": {"items": [{**data["items"][0], "qty": "30"}]}, "filing": {"iec": "ABCDE1234H"}}, headers=h).json()
    log = [e for e in edit["events"] if e["type"] in ("CHA_DATA_CHANGED", "CHA_FILING_CHANGED")]
    assert any(e["type"] == "CHA_FILING_CHANGED" and e["detail"]["changes"][0]["field"] == "iec" and e["by"] for e in log)
    assert any(e["type"] == "CHA_DATA_CHANGED" and e["detail"]["section"] == "items" and e["detail"]["changed"] == [1] for e in log)
    # cost and selling on the job: the totals reach every CHA row
    put = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [{"charge_name": "Customs Clearance", "uom": "Per Shipment", "qty": "1", "buy": {"ccy": "INR", "exch": "1", "rate": "3000", "tax_type": "Non-Taxable", "tax_pct": "0", "party": "Broker"},
                                                                         "sell": {"ccy": "INR", "exch": "1", "rate": "4500", "tax_type": "Taxable", "tax_pct": "18", "party": "Imp Co"}}]}, headers=h)
    assert put.status_code == 200
    row = client.get(f"/api/cha/jobs/{no}", headers=h).json()
    assert row["charges_cost"] == 3000 and row["charges_sell"] == 4500.0


def test_export_job_tabs_follow_the_shipping_bill_screens(h):
    exp = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "Switch Intl", "importer": "Armin Rub", "pol": "INMAA", "pod": "HKHKG"}, headers=h).json()
    no = exp["cha_job_number"]
    sections = {c["key"]: [s["title"] for s in c.get("sections", [])] for t in exp["spec"] for c in t["collections"]}
    assert sections["invoices"] == ["Invoice main details", "Freight & insurance", "Third party (SW)"] and sections["items"] == ["Item main details", "Other details", "SW - manufacturer", "Single window", "SW constituent", "SW production", "SW control", "SW statement", "Supporting documents"]
    assert {"ar4", "annexure_c1", "eway", "notes", "billto"} <= set(sections)
    data = {"invoices": [{"invoice_no": "4363365", "invoice_date": "2026-10-19", "invoice_value": "436534.34", "currency": "USD", "exchange_rate": "94.2", "terms": "CIF", "freight_amt": "45.6", "insurance_amt": "65.5"}],
            "items": [{"invoice_sr": "1", "description": "RUBBER PARTS", "qty": "100", "scheme": "00-FREE SHIPPING BILL", "rodtep": "Yes", "mfr_country": "INDIA"}], "ar4": [{"ar4_no": "AR4-1", "commissionerate": "Chennai"}],
            "annexure_c1": [{"exporter_name": "Switch Intl", "iec_no": "0494028467", "seal_no": "S123"}], "billto": [{"party_name": "Shahi Export Pvt Ltd", "gstn": "29AAJCS115L1ZU", "state_name": "29--KARNATAKA"}], "notes": [{"note_text": "Docs received"}]}
    ok = client.patch(f"/api/cha/jobs/{no}", json={"data": data, "filing": {"bank_account": "2656574774", "ifsc_code": "SBIN0000001", "nfei": "No"}}, headers=h)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["data"]["invoices"][0]["freight_amt"] == "45.6" and body["data"]["billto"][0]["party_name"] == "Shahi Export Pvt Ltd" and body["filing"]["bank_account"] == "2656574774"
    # an Import-only section is refused on an Export job
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": {"igm": [{"igm_no": "1"}]}}, headers=h).status_code == 422


def test_job_documents_move_to_accounts_and_job_status(h):
    import base64

    j = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "Doc Co", "importer": "Doc Buyer", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
    no = j["cha_job_number"]
    raw = b"%PDF-1.4 test document"
    up = client.post(f"/api/cha/files/{no}", json={"name": "BL copy.pdf", "content_type": "application/pdf", "data_base64": base64.b64encode(raw).decode()}, headers=h)
    assert up.status_code == 201 and up.json()["size"] == len(raw)
    lst = client.get(f"/api/cha/files/{no}", headers=h).json()["items"]
    assert [f["name"] for f in lst] == ["BL copy.pdf"]
    dl = client.get(f"/api/cha/file/{lst[0]['id']}", headers=h)
    assert dl.status_code == 200 and dl.content == raw and dl.headers["content-type"].startswith("application/pdf")
    assert client.post(f"/api/cha/files/{no}", json={"name": "x", "data_base64": "!!!"}, headers=h).status_code == 422
    assert any(e["type"] == "CHA_FILE_ADDED" for e in client.get(f"/api/cha/jobs/{no}", headers=h).json()["events"])
    # move to Accounts needs charges and a bill-to party; afterwards both are locked
    assert client.post(f"/api/cha/jobs/{no}/move-to-accounts", headers=h).status_code == 422
    line = {"charge_name": "Customs Clearance", "uom": "Per Shipment", "qty": "1", "buy": {"ccy": "INR", "exch": "1", "rate": "3000", "tax_type": "Non-Taxable", "tax_pct": "0", "party": "Broker"}, "sell": {"ccy": "INR", "exch": "1", "rate": "4500", "tax_type": "Taxable", "tax_pct": "18", "party": "Doc Co"}}
    assert client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [line]}, headers=h).status_code == 200
    assert client.post(f"/api/cha/jobs/{no}/move-to-accounts", headers=h).status_code == 422          # still no bill-to party
    client.patch(f"/api/cha/jobs/{no}", json={"data": {"billto": [{"party_name": "Doc Co", "gstn": "29AAJCS115L1ZU"}]}}, headers=h)
    moved = client.post(f"/api/cha/jobs/{no}/move-to-accounts", headers=h)
    assert moved.status_code == 200 and moved.json()["accounts_moved"]["by"]
    assert client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [line, line]}, headers=h).status_code == 409
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": {"billto": [{"party_name": "Someone Else"}]}}, headers=h).status_code == 409
    assert client.post(f"/api/cha/jobs/{no}/move-to-accounts", headers=h).status_code == 409
    assert client.post(f"/api/cha/jobs/{no}/reopen-from-accounts", json={"reason": "no"}, headers=h).status_code == 422
    back = client.post(f"/api/cha/jobs/{no}/reopen-from-accounts", json={"reason": "Rates revised"}, headers=h)
    assert back.status_code == 200 and not back.json()["accounts_moved"]
    assert client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [line, line]}, headers=h).status_code == 200
    assert back.json()["closure_status"] == "OPEN"
    sw = {s["title"] for t in j["spec"] for c in t["collections"] for s in c.get("sections", [])}
    assert {"SW constituent", "SW production", "SW control", "SW statement"} <= sw
    fid = lst[0]["id"]
    assert client.delete(f"/api/cha/file/{fid}", headers=h).status_code == 204 and client.get(f"/api/cha/files/{no}", headers=h).json()["items"] == []


def test_order_of_work_is_enforced_export_cha_first_import_freight_first(h):
    # EXPORT: the freight job cannot go On Transit until its CHA jobs are cleared
    done = booking(h, "Freight, CHA")
    fj = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Forwarding")
    cj = next(j["job_number"] for j in done["jobs"] if j["job_type"] == "Customs")
    blocked = client.post(f"/api/jobs/{fj}/stage", json={"stage": "On Transit", "reason": "cargo sailed"}, headers=h)
    assert blocked.status_code == 409, blocked.text
    d = blocked.json()["detail"]
    assert d["code"] == "ORDER_OF_WORK" and cj in d["message"] and "CHA job comes first" in d["message"] and d["can_override"] is True
    for st in ("VALIDATED", "CUSTOMS FILED", "OOC / LEO CLEARED"):
        assert client.patch(f"/api/cha/jobs/{cj}/status", json={"status": st}, headers=h).status_code == 200
    assert client.post(f"/api/jobs/{fj}/stage", json={"stage": "On Transit", "reason": "cargo sailed"}, headers=h).status_code == 200
    # an authorised override with a reason is allowed and logged
    done2 = booking(h, "Freight, CHA")
    fj2 = next(j["job_number"] for j in done2["jobs"] if j["job_type"] == "Forwarding")
    ok = client.post(f"/api/jobs/{fj2}/stage", json={"stage": "On Transit", "reason": "Customer instruction", "confirm_conflict": True}, headers=h)
    assert ok.status_code == 200, ok.text
    # IMPORT: the CHA job cannot be filed until its freight job is On Transit
    imp = booking(h, "Freight, CHA", direction="Import", mode="Sea")
    ifj = next(j["job_number"] for j in imp["jobs"] if j["job_type"] == "Forwarding")
    icj = next(j["job_number"] for j in imp["jobs"] if j["job_type"] == "Customs")
    assert client.patch(f"/api/cha/jobs/{icj}/status", json={"status": "DOCUMENTATION"}, headers=h).status_code == 200          # preparing is fine
    nope = client.patch(f"/api/cha/jobs/{icj}/status", json={"status": "CUSTOMS FILED"}, headers=h)
    assert nope.status_code == 409 and nope.json()["detail"]["code"] == "ORDER_OF_WORK" and ifj in nope.json()["detail"]["message"]
    assert client.patch(f"/api/cha/jobs/{icj}/status", json={"status": "CUSTOMS FILED", "override": True}, headers=h).status_code == 422       # a reason is needed
    ovr = client.patch(f"/api/cha/jobs/{icj}/status", json={"status": "CUSTOMS FILED", "override": True, "remarks": "Urgent clearance"}, headers=h)
    assert ovr.status_code == 200
    assert any("out of order" in e["text"] for e in client.get(f"/api/cha/jobs/{icj}", headers=h).json()["events"])
    # once the freight job is On Transit the order is satisfied
    imp2 = booking(h, "Freight, CHA", direction="Import", mode="Sea")
    ifj2 = next(j["job_number"] for j in imp2["jobs"] if j["job_type"] == "Forwarding")
    icj2 = next(j["job_number"] for j in imp2["jobs"] if j["job_type"] == "Customs")
    assert client.post(f"/api/jobs/{ifj2}/stage", json={"stage": "On Transit", "reason": "vessel sailed"}, headers=h).status_code == 200
    assert client.patch(f"/api/cha/jobs/{icj2}/status", json={"status": "CUSTOMS FILED"}, headers=h).status_code == 200



def test_icegate_json_for_export_follows_the_sb_sample(h):
    j = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Sea", "exporter": "SWITCH INTERNATIONAL", "importer": "ARMIN RUB", "pol": "INMAA1", "pod": "HKSTN", "customs_location": "INMAA1"}, headers=h).json()
    no = j["cha_job_number"]
    data = {"invoices": [{"invoice_no": "4363365", "invoice_date": "2026-10-19", "currency": "USD", "exchange_rate": "94.2", "terms": "CIF", "freight_amt": "45.6", "insurance_amt": "65.5", "contract_no": "46457572348", "payment_method": "NA",
                          "third_party_name": "Global Fluidtech Systems", "third_party_city": "Ahmedabad", "third_party_pin": "380013", "buyer_name": "SAME AS CONSIGNEE"}],
            "items": [{"invoice_sr": "1", "description": "TEST", "qty": "10", "qty_unit": "KGS", "unit_price": "100", "per": "1", "ritc_no": "87141090", "scheme": "00-FREE SHIPPING BILL", "reward": "No", "end_use": "GNX100", "igst_payment_status": "LUT",
                      "amount": "1000", "sw_info_type": "DTY", "sw_info_qualifier": "RDT", "sw_info_code": "RODTEPN", "sw_info_text": "NOT CLAIMED", "sw_info_msr": "10", "sw_info_uqc": "KGS"}]}
    filing = {"iec": "0494028467", "branch_sr_no": "1", "exporter_name": "SWITCH INTERNATIONAL (A DIVN. OF ELBRASI", "addr1": "AUTOMOTIVE PRODUCTS", "addr2": "NEHRU NAGAR", "addr3": "KOTTIVAKKAM,CHENNAI", "state": "TAMIL NADU", "state_of_origin": "33",
              "exporter_type": "R - Merchant Exporter", "exporter_class": "P - Private", "ad_code": "3445533", "consignee": "ARMIN RUB", "cons_addr1": "LINDENWIESE 25", "cons_country": "DE", "port_loading": "INMAA1", "port_discharge": "HKSTN",
              "port_final_destination": "HKSTN", "country_final_destination": "HK", "country_discharge": "HK", "marks": "TEST", "gstn_type": "GSN - GSTIN of Normal Persons", "gstn_id": "4536667896"}
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": data, "filing": filing, "customs_location": "INMAA1"}, headers=h).status_code == 200
    r = client.get(f"/api/cha/jobs/{no}/icegate-json", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    msg = out["message"]
    assert out["filename"].startswith("SB") and out["filename"].endswith(".json")
    assert set(msg["headerField"]) == {"senderID", "receiverID", "indicator", "messageID", "sequenceOrControlNumber", "jobNumber", "jobDate", "messageType"}
    assert msg["headerField"]["receiverID"] == "INMAA1" and msg["headerField"]["messageID"] == "CACHE01" and msg["headerField"]["messageType"] == "F"
    assert {"exchangeModel", "invoiceModel", "itemModel", "sbModel", "infoType"} <= set(msg["master"])
    assert msg["master"]["exchangeModel"] == [{"invoiceCurrencyCode": "USD", "unitInRs": "1", "rate": "94.2", "whetherStandardCurrency": "Y"}]
    inv = msg["master"]["invoiceModel"][0]
    assert inv["actualInvoiceNo"] == "4363365" and inv["invoiceDate"] == "20261019" and inv["natureOfContract"] == "CIF" and inv["freightAmount"] == "45.6" and inv["insuranceAmount"] == "65.5" and inv["thirdPartyName"] == "Global Fluidtech Systems"
    sb = msg["master"]["sbModel"][0]
    assert sb["importerExporterCode"] == "0494028467" and sb["typeOfExporter"] == "R" and sb["exporterClass"] == "P" and sb["stateOfOriginExporter"] == "33" and sb["gstnType"] == "GSN" and sb["portOfLoading"] == "INMAA1" and sb["countryOfFinalDestination"] == "HK"
    it = msg["master"]["itemModel"][0]
    assert it["ritcCode"] == "87141090" and it["quantity"] == "10.000" and it["unitPrice"] == "100.00000" and it["schemeCode"] == "00" and it["rewardItem"] == "N" and it["igstPaymentStatus"] == "LUT"
    assert msg["master"]["infoType"][0]["infoType"] == "DTY" and msg["master"]["infoType"][0]["infoCode"] == "RODTEPN"
    assert any("ICEGATE ID" in w for w in out["warnings"])          # nothing names the sender yet


def test_icegate_json_for_import_follows_the_be_sample(h):
    j = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Air", "exporter": "SUPPLIER", "importer": "TESTING", "pol": "SIN", "pod": "INMAA4", "customs_location": "INMAA4"}, headers=h).json()
    no = j["cha_job_number"]
    data = {"invoices": [{"invoice_no": "7534451158", "invoice_date": "2025-07-10", "currency": "USD", "exchange_rate": "97.05", "terms": "FOB", "invoice_value": "886526.03", "supplier_name": "SUPPLIER", "supp_address1": "CHENNAI", "supp_address2": "asdfvcb",
                          "supp_country": "INDIA", "supp_pin": "600032", "freight_charge": "177305.21", "freight_ccy": "INR", "insurance_charge": "15222", "insurance_ccy": "INR", "misc_charge": "7000", "misc_ccy": "INR"}],
            "items": [{"invoice_sr": "1", "description": "TEST", "qty": "24", "qty_unit": "KGS", "unit_price": "8865.2603", "ritc_no": "29199090", "cth_no": "29199090", "country_origin": "SG", "end_use": "GNX100", "scheme_code": "03", "scheme_notn": "018/2015", "scheme_notn_sl": "1",
                      "basic_duty_notn": "045/2025", "levy_notn_no": "009/2025", "levy_notn_sl": "I230", "levy_pct": "5", "levy_cp_cess_notn_no": "001/2017", "aid_cess_notn": "011/2021", "info_msr": "100", "info_uqc": "KGS"}]}
    filing = {"be_type": "X - Ex-bond", "iec": "TESTINGTES", "importer_name": "TESTING", "addr1": "ADD1", "addr2": "ADD2", "addr3": "TESTING", "state": "TAMIL NADU", "mode_of_transport": "A - Air", "port_origin": "SIN", "port_shipment": "SIN", "country_origin": "SG",
              "country_consignment": "SG", "ad_code": "TESTING", "payment_method": "T - Transactional", "commercial_tax_type": "G - GST-IN", "commercial_tax_reg_no": "TESTINGTES123TE", "high_sea_sale": "No - No"}
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": data, "filing": filing, "customs_location": "INMAA4"}, headers=h).status_code == 200
    out = client.get(f"/api/cha/jobs/{no}/icegate-json", headers=h).json()
    msg = out["message"]
    assert out["filename"].startswith("BE") and msg["headerField"]["messageID"] == "CACHI01" and msg["headerField"]["receiverID"] == "INMAA4"
    assert {"beModel", "exchangeModel", "invoiceModel", "itemsModel", "depbModel", "sbedutyModel", "ctxModel", "infoTypeModel", "statementModel"} <= set(msg["master"])
    be = msg["master"]["beModel"][0]
    assert be["beType"] == "X" and be["iecCode"] == "TESTINGTES" and be["nameOfImporter"] == "TESTING" and be["modeOfTransport"] == "A" and be["portOfOrigin"] == "SIN" and be["countryOfOrigin"] == "SG" and be["paymentMethodCode"] == "T" and be["highSeaSaleFlag"] == "N"
    assert [x["currencyCode"] for x in msg["master"]["exchangeModel"]] == ["USD", "INR"]
    inv = msg["master"]["invoiceModel"][0]
    assert inv["actualInvoiceNumber"] == "7534451158" and inv["invoiceValue"] == "886526.03" and inv["freightValue"] == "177305.21" and inv["insuranceValue"] == "15222" and inv["miscCharges"] == "7000" and inv["natureOfTransaction"] == "S"
    it = msg["master"]["itemsModel"][0]
    assert it["quantity"] == "24.000000" and it["unitPriceInvoiced"] == "8865.260300" and it["cth"] == "29199090" and it["ceth"] == "NOEXCISE" and it["bcdNotification"] == "045/2025"
    assert msg["master"]["depbModel"][0]["bcdNotification"] == "018/2015"
    assert {(d["addlDutyFlag"], d["notificationNo"]) for d in msg["master"]["sbedutyModel"]} >= {("I", "009/2025"), ("P", "001/2017"), ("A", "011/2021")}
    assert msg["master"]["ctxModel"][0] == {"stateCode": "33", "commercialTaxType": "G", "commercialTaxRegistrationNo": "TESTINGTES123TE"}
    assert [s["statementCode"] for s in msg["master"]["statementModel"]] == ["CUG00", "CUG01", "CUV01", "CUV02", "CUV03"]


def _filled_import(h):
    j = client.post("/api/cha/jobs", json={"job_type": "Import", "mode": "Sea", "exporter": "SUPPLIER", "importer": "TESTING", "pol": "SIN", "pod": "INMAA1", "customs_location": "INMAA1"}, headers=h).json()
    no = j["cha_job_number"]
    data = {"invoices": [{"invoice_no": "INV1", "invoice_date": "2026-10-10", "currency": "USD", "exchange_rate": "90", "terms": "FOB", "invoice_value": "1000", "supplier_name": "SUPPLIER", "freight_charge": "100", "freight_ccy": "USD"}],
            "items": [{"invoice_sr": "1", "description": "TEST", "qty": "10", "qty_unit": "KGS", "unit_price": "100", "ritc_no": "29199090", "cth_no": "29199090", "country_origin": "SG"}]}
    assert client.patch(f"/api/cha/jobs/{no}", json={"data": data, "filing": {"be_type": "H - Home Consumption", "importer_name": "TESTING", "mode_of_transport": "S - Sea"}}, headers=h).status_code == 200
    return no


def test_checklist_import_and_export_and_annexure(h):
    no = _filled_import(h)
    r = client.get(f"/api/cha/jobs/{no}/checklist", headers=h)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["direction"] == "Import" and c["job_no"] and len(c["items"]) == 1
    assert c["totals"]["av"] > 0 and c["duty_words"]
    e = client.post("/api/cha/jobs", json={"job_type": "Export", "mode": "Air", "exporter": "SWITCH", "importer": "ARMIN", "pol": "INMAA4", "pod": "HKHKG", "customs_location": "INMAA4"}, headers=h).json()["cha_job_number"]
    assert client.patch(f"/api/cha/jobs/{e}", json={"data": {"invoices": [{"invoice_no": "A1", "invoice_date": "2026-10-19", "currency": "USD", "exchange_rate": "94", "terms": "FOB"}], "items": [{"invoice_sr": "1", "description": "T", "qty": "10", "qty_unit": "KGS", "unit_price": "100", "per": "1", "ritc_no": "87141090", "amount": "1000"}]}}, headers=h).status_code == 200
    c = client.get(f"/api/cha/jobs/{e}/checklist", headers=h)
    assert c.status_code == 200, c.text
    assert c.json()["direction"] == "Export" and c.json()["totals"]["fob"] > 0
    a = client.get(f"/api/cha/jobs/{e}/annexure-c", headers=h)
    assert a.status_code == 200, a.text
    assert a.json()["mode"] == "Air" and a.json()["job_no"]
