import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(scope="module")
def dev(world):
    return hdr(world["dev"], portal="developer")


def test_global_import_is_platform_only_and_idempotent(h, dev):
    assert client.post("/api/ports/import-global", headers=h).status_code == 403  # a company cannot load the platform master
    first = client.post("/api/ports/import-global", headers=dev)
    assert first.status_code == 200 and first.json()["file_rows"] > 21000
    again = client.post("/api/ports/import-global", headers=dev).json()
    assert again["created"] == 0 and again["skipped"] == again["file_rows"]


def test_worldwide_sea_ports_and_airports_are_present_and_searchable(h, dev):
    client.post("/api/ports/import-global", headers=dev)
    s = client.get("/api/ports/summary", headers=h).json()
    assert s["types"]["Sea Port"] > 15000 and s["types"]["Airport"] > 3500 and "India" in s["countries"] and "Singapore" in s["countries"] and len(s["countries"]) > 200
    maa = client.get("/api/ports", params={"q": "MAA"}, headers=h)
    codes = [p["code"] for p in maa.json()]
    assert codes[0] == "MAA" and "INMAA" in codes  # exact code first: the airport and the sea port of Chennai
    sea = next(p for p in maa.json() if p["code"] == "INMAA")
    assert sea["port_type"] == "Sea Port" and sea["unlocode"] == "INMAA" and sea["country"] == "India"
    for code, kind in (("AEJEA", "Sea Port"), ("SGSIN", "Sea Port"), ("DEHAM", "Sea Port"), ("BEANR", "Sea Port"), ("CNSHG", "Sea Port"), ("DXB", "Airport"), ("SIN", "Airport"), ("LHR", "Airport"), ("FRA", "Airport"), ("BOM", "Airport")):
        r = client.get("/api/ports", params={"q": code}, headers=h).json()
        assert any(p["code"] == code and p["port_type"] == kind for p in r), code


def test_filters_paging_and_total_count_header(h, dev):
    client.post("/api/ports/import-global", headers=dev)
    page1 = client.get("/api/ports", params={"country": "India", "port_type": "Sea Port", "limit": 10}, headers=h)
    total = int(page1.headers["X-Total-Count"])
    assert len(page1.json()) == 10 and 50 < total < 500 and all(p["country"] == "India" and p["port_type"] == "Sea Port" for p in page1.json())
    page2 = client.get("/api/ports", params={"country": "India", "port_type": "Sea Port", "limit": 10, "offset": 10}, headers=h).json()
    assert {p["code"] for p in page1.json()}.isdisjoint({p["code"] for p in page2})
    assert client.get("/api/ports", params={"limit": 5000}, headers=h).status_code == 422  # the page size is capped
    assert int(client.get("/api/ports", params={"q": "zzzzzzzz"}, headers=h).headers["X-Total-Count"]) == 0


def test_companies_can_read_but_never_change_the_platform_master(h, dev):
    client.post("/api/ports/import-global", headers=dev)
    mine = client.get("/api/ports", params={"q": "INMAA"}, headers=h).json()[0]
    assert client.patch(f"/api/ports/{mine['id']}", json={"name": "Hacked"}, headers=h).status_code == 403
    assert client.delete(f"/api/ports/{mine['id']}", headers=h).status_code == 403
    assert client.post("/api/ports", json={"code": "ZZTEST", "port_type": "Sea Port", "name": "Test Port", "country": "India"}, headers=h).status_code == 403
    # a platform administrator's own edit survives a re-import
    assert client.patch(f"/api/ports/{mine['id']}", json={"timezone": "Asia/Kolkata"}, headers=dev).status_code == 200
    client.post("/api/ports/import-global", headers=dev)
    assert client.get("/api/ports", params={"q": "INMAA"}, headers=h).json()[0]["timezone"] == "Asia/Kolkata"
