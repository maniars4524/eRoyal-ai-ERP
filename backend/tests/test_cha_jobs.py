from datetime import date, timedelta

import pytest

from tests.test_bookings import client, draft, hdr  # noqa: F401  (same login helpers and fixtures)


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def released(h, **data):
    b = draft(h, data={"pcs": "10", "services": "Freight, CHA", "consignee": "Solvay SA", **data})
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
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
    done = client.post(f"/api/bookings/{b['id']}/confirm", headers=h).json()
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
