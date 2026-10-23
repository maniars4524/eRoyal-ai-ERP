"""Global search: one keyword finds parties, users and more inside the caller's own company only; short queries are refused; no token, no search."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def hdr(user):
    r = client.post("/api/auth/login", json={"identifier": user["username"], "password": PASSWORD, "portal": "erp", "license_number": user["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_search_finds_records_of_own_company_only(world, admin, admin2):
    h = hdr(admin)
    word = "Zq" + uuid.uuid4().hex[:6]
    made = client.post("/api/customers", json={"code": word[:8].upper(), "name": word + " Traders", "roles": ["shipper"], "data": {}}, headers=h)
    assert made.status_code in (200, 201), made.text
    out = client.get("/api/search", params={"q": word}, headers=h).json()
    titles = {g["title"]: g["items"] for g in out["groups"]}
    assert any(word in i["label"] for i in titles.get("Organizations", []))
    assert any(i["go"] == "org-list" for i in titles["Organizations"])
    users = client.get("/api/search", params={"q": admin["username"]}, headers=h).json()
    assert any(admin["username"] in i["sub"] for g in users["groups"] if g["title"] == "Users" for i in g["items"])


def test_search_is_validated_and_authenticated(admin):
    h = hdr(admin)
    assert client.get("/api/search", params={"q": "a"}, headers=h).status_code == 422
    assert client.get("/api/search", params={"q": "abc"}).status_code == 401
    assert client.get("/api/search", params={"q": "%%"}, headers=h).status_code == 200


def test_party_defaults_follow_gst_applicable(world, admin):
    h = hdr(admin)
    tag = uuid.uuid4().hex[:5].upper()
    for name, country, gstin, data, expect in [("GstNo " + tag, "India", "", {"gst_applicable": "No"}, "No"), ("Abroad " + tag, "Germany", "", {}, "No"), ("Local " + tag, "India", "", {}, "Yes")]:
        made = client.post("/api/customers", json={"code": ("G" + uuid.uuid4().hex[:6]).upper(), "name": name, "country": country, "roles": ["ar"], "data": data}, headers=h)
        assert made.status_code in (200, 201), made.text
        pd = client.get("/api/documents/billing/party-defaults", params={"party": name, "doc_type": "invoice"}, headers=h).json()
        assert pd["gst_applicable"] == expect, (name, pd)
        assert pd["party_gstin"] == ""
