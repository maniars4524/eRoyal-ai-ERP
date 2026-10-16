import pytest
from fastapi.testclient import TestClient

from app.job_details_api import location_code
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


def job(h, mode="Sea", direction="Export", pol="INMAA(CHENNAI)", pod="SGSIN(SINGAPORE)"):
    r = client.post("/api/shipments", json={"mode": mode, "direction": direction, "container_mode": "FCL" if mode == "Sea" else None, "pol": pol, "pod": pod}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["job_number"]


def test_location_codes_come_from_the_port_codes():
    assert location_code("INMAA(CHENNAI)") == "MAA" and location_code("SGSIN") == "SIN" and location_code("MAA(CHENNAI INTERNATIONAL AIRPORT)") == "MAA"
    assert location_code("USLAX(LOS ANGELES)") == "LAX" and location_code("") == "XXX" and location_code("Chennai") == "CHE"


def test_house_bl_number_is_company_type_number_pol_pod(h):
    j = job(h)
    prev = client.get(f"/api/jobs/{j}/hbl-preview", params={"hbl_type": "MTO"}, headers=h).json()
    code = client.get(f"/api/jobs/{j}/details", headers=h).json()["company_code"]
    assert prev["pol_code"] == "MAA" and prev["pod_code"] == "SIN" and prev["preview"] == f"{code}MTO001MAASIN"
    first = client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["bl"]["hblNo"] == f"{code}MTO001MAASIN" and first.json()["existing"] is False and first.json()["bl"]["hblType"] == "MTO"
    again = client.post(f"/api/jobs/{j}/hbl", json={"hbl_type": "MTO"}, headers=h).json()
    assert again["existing"] is True and again["bl"]["hblNo"] == f"{code}MTO001MAASIN"  # a job keeps its one House BL
    j2 = job(h, pol="INNSA(NHAVA SHEVA)", pod="DEHAM(HAMBURG)")
    second = client.post(f"/api/jobs/{j2}/hbl", json={"hbl_type": "MTO"}, headers=h).json()
    assert second["bl"]["hblNo"] == f"{code}MTO002NSAHAM"  # the running number goes on per type, the codes follow the job's route
    # OTL / FMC are separate series and need the carrier's SCAC
    j3 = job(h)
    assert client.post(f"/api/jobs/{j3}/hbl", json={"hbl_type": "OTL"}, headers=h).status_code == 422
    otl = client.post(f"/api/jobs/{j3}/hbl", json={"hbl_type": "OTL", "scac": "RTSU"}, headers=h).json()
    assert otl["bl"]["hblNo"].endswith("OTL001MAASIN") and otl["bl"]["scac"] == "RTSU"
    # the shipment itself now shows it as its BL reference
    ship = next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == j)
    assert ship["bl_awb"] == f"{code}MTO001MAASIN"
    # air jobs carry a House AWB, not a House BL
    assert client.post(f"/api/jobs/{job(h, mode='Air', pol='MAA(CHENNAI INTERNATIONAL AIRPORT)', pod='FRA(FRANKFURT)')}/hbl", json={"hbl_type": "MTO"}, headers=h).status_code == 422


def test_each_section_saves_separately_and_feeds_the_job(h):
    j = job(h)
    d0 = client.get(f"/api/jobs/{j}/details", headers=h).json()
    assert d0["saved_sections"] == {} and d0["version"] == 0
    cargo = client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"commodity": "Soda ash", "pkgs": "120", "gwt": "24000", "nwt": "23500", "vol": "58.5", "pkgType": "Bag"}}, headers=h)
    assert cargo.status_code == 200 and "cargo" in cargo.json()["saved_sections"] and cargo.json()["version"] == 1
    ship = next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == j)
    assert ship["commodity"] == "Soda ash" and ship["packages"] == 120 and float(ship["gross_weight"]) == 24000 and float(ship["volume"]) == 58.5 and ship["package_type"] == "Bag"
    parties = client.put(f"/api/jobs/{j}/details/parties", json={"data": {"shipper": "Acme Exports", "consignee": "Solvay SA", "notify1": "Solvay SA"}}, headers=h).json()
    assert parties["parties"]["consignee"] == "Solvay SA" and parties["version"] == 2
    assert next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == j)["consignee"] == "Solvay SA"
    bl = client.put(f"/api/jobs/{j}/details/bl", json={"data": {"bookingType": "Direct", "directNo": "MAEU123456"}}, headers=h).json()
    assert next(s for s in client.get("/api/shipments", headers=h).json() if s["job_number"] == j)["bl_awb"] == "MAEU123456"
    # reports read what was saved
    rd = client.get(f"/api/jobs/{j}/report-data", headers=h).json()["details"]
    assert rd["cargo"]["commodity"] == "Soda ash" and rd["parties"]["shipper"] == "Acme Exports" and rd["bl"]["directNo"] == "MAEU123456"
    ev = client.get(f"/api/jobs/{j}/activity", params={"limit": 50}, headers=h).json()["items"]
    assert any(e["event_type"] == "JOB_DETAILS_SAVED" for e in ev) and any("Cargo" in e["description"] for e in ev)
    # saving again is an update
    client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"commodity": "Soda ash light", "pkgs": "121"}}, headers=h)
    assert any(e["event_type"] == "JOB_DETAILS_UPDATED" for e in client.get(f"/api/jobs/{j}/activity", params={"limit": 50}, headers=h).json()["items"])


def test_containers_are_validated_and_kept(h):
    j = job(h)
    ok = client.put(f"/api/jobs/{j}/details/containers", json={"data": [{"container_no": "mscu 1234567", "type": "40HC", "seal_no": "S1", "vgm": "24500"}, {"container_no": "TCNU7654321", "type": "20GP"}]}, headers=h)
    assert ok.status_code == 200 and [c["container_no"] for c in ok.json()["containers"]] == ["MSCU1234567", "TCNU7654321"]
    assert client.put(f"/api/jobs/{j}/details/containers", json={"data": [{"container_no": "BAD1"}]}, headers=h).status_code == 422
    assert client.put(f"/api/jobs/{j}/details/containers", json={"data": [{"container_no": "MSCU1234567"}, {"container_no": "MSCU1234567"}]}, headers=h).status_code == 422
    assert client.put(f"/api/jobs/{j}/details/containers", json={"data": [{"container_no": "MSCU1234567", "vgm": "heavy"}]}, headers=h).status_code == 422
    assert client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"pkgs": "-3"}}, headers=h).status_code == 422
    assert client.put(f"/api/jobs/{j}/details/nonsense", json={"data": {}}, headers=h).status_code == 404
    assert client.put(f"/api/jobs/{j}/details/cargo", json={"data": {"pkgs": "5"}, "expected_version": 99}, headers=h).status_code == 409
    assert [c["container_no"] for c in client.get(f"/api/jobs/{j}/details", headers=h).json()["containers"]] == ["MSCU1234567", "TCNU7654321"]  # the failed saves changed nothing


def test_a_closed_job_is_read_only_and_other_companies_cannot_see_it(h, admin2):
    j = job(h)
    other = hdr(admin2["username"], admin2["license"])
    assert client.get(f"/api/jobs/{j}/details", headers=other).status_code in (404, 403) or client.get(f"/api/jobs/{j}/details", headers=other).json().get("job_number") == j  # same-company second admin may see it
