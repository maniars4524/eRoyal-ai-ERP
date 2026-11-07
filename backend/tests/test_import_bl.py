"""Import jobs: HBL / MBL numbers are typed (never generated), no HBL clauses, no BL confirm / release. Export jobs are unchanged."""
import pytest

from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def mk(h, mode, direction):
    r = client.post("/api/shipments", json={"mode": mode, "direction": direction, "container_mode": "FCL" if mode == "Sea" else "Air", "pol": "SGSIN", "pod": "INMAA"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["job_number"]


def test_an_import_job_gets_no_generated_hbl(h):
    j = mk(h, "Sea", "Import")
    r = client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h)
    assert r.status_code == 409 and "import" in r.json()["detail"].lower()
    a = mk(h, "Air", "Import")
    assert client.post(f"/api/jobs/{a}/hbl", json={"hbl_type": "HAWB"}, headers=h).status_code == 409


def test_the_numbers_are_typed_by_the_user_and_can_be_corrected(h):
    j = mk(h, "Sea", "Import")
    r = client.put(f"/api/jobs/{j}/details/bl", json={"data": {"bookingType": "House", "mblNo": "MBL-9001", "hblNo": "ORIGIN-HBL-77", "hblDate": "2030-01-05"}}, headers=h)
    assert r.status_code == 200 and r.json()["bl"]["hblNo"] == "ORIGIN-HBL-77" and r.json()["bl"]["mblNo"] == "MBL-9001"
    r = client.put(f"/api/jobs/{j}/details/bl", json={"data": {"bookingType": "House", "mblNo": "MBL-9001", "hblNo": "ORIGIN-HBL-78"}}, headers=h)
    assert r.json()["bl"]["hblNo"] == "ORIGIN-HBL-78"                                          # a correction is allowed (an export HBL number is protected)


def test_no_stage_release_or_clauses_for_import(h):
    j = mk(h, "Sea", "Import")
    client.put(f"/api/jobs/{j}/details/bl", json={"data": {"bookingType": "House", "mblNo": "M1", "hblNo": "H1", "blStage": "Released", "clauses": [{"text": "x"}]}}, headers=h)
    bl = client.get(f"/api/jobs/{j}/details", headers=h).json()["bl"]
    assert not bl.get("blStage") and not bl.get("clauses")
    assert client.post(f"/api/jobs/{j}/bl/stage", json={"stage": "Confirmed"}, headers=h).status_code == 409
    assert client.put(f"/api/jobs/{j}/hbl-clauses", json={"clauses": []}, headers=h).status_code in (409, 422)


def test_export_jobs_still_generate_the_hbl(h):
    j = mk(h, "Sea", "Export")
    r = client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["bl"]["hblNo"] and r.json()["bl"]["hblType"] == "MTO"
    again = client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h).json()
    assert again.get("existing") is True and again["bl"]["hblNo"] == r.json()["bl"]["hblNo"]
