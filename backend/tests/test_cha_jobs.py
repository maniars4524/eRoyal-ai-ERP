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
