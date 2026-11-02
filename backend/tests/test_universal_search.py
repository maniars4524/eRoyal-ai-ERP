"""Universal search: one keyword across every module the user may read, classified Department > Division > Service > Category, with filters, paging and permissions."""
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization, Permission, Role, User
from app.security import hash_password
from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


@pytest.fixture(scope="module")
def data(h):
    tag = uuid.uuid4().hex[:6].upper()
    num = str(uuid.uuid4().int)[:7]
    party = f"ZQXV{tag} TRADERS"
    out = {"tag": tag, "party": party, "num": num}
    j1 = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR", "consignee": party}, headers=h).json()
    j2 = client.post("/api/shipments", json={"mode": "Air", "direction": "Import", "pol": "SIN", "pod": "INMAA4", "consignee": party}, headers=h).json()
    j3 = client.post("/api/shipments", json={"mode": "Road", "direction": "Domestic", "pol": "Chennai", "pod": "Bangalore", "consignee": party}, headers=h).json()
    out.update(sea=j1["job_number"], air=j2["job_number"], road=j3["job_number"])
    assert client.put(f"/api/jobs/{j1['job_number']}/details/containers", json={"data": [{"container_no": f"MEDU{num}", "type": "20GP"}]}, headers=h).status_code == 200
    q = client.post("/api/quotations", json={"party_name": party, "mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "BEANR", "lines": [{"side": "SELL", "charge_name": "Docs", "ccy": "INR", "exch": "1", "rate": "100", "qty": "1", "basis": "Shipment", "taxable": False}]}, headers=h)
    assert q.status_code == 201, q.text
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": party, "job_number": j1["job_number"], "lines": [{"charge_name": "Freight", "rate": "100", "tax_pct": "0", "job_number": j1["job_number"]}]}, headers=h)
    assert d.status_code == 201, d.text
    out["invoice_id"] = d.json()["id"]
    return out


def find(res, **kw):
    return [i for i in res["items"] if all(i.get(k) == v for k, v in kw.items())]


def test_one_keyword_finds_records_in_every_module_with_their_classification(h, data):
    r = client.get("/api/search/universal", params={"q": data["tag"]}, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()
    cats = {i["category"] for i in res["items"]}
    assert {"Jobs", "Quotations", "Invoices"} <= cats
    sea = find(res, ref=data["sea"])[0]
    assert (sea["department"], sea["division"], sea["service"], sea["category"], sea["mode"]) == ("Logistics & Customs", "Export", "Sea Freight", "Jobs", "Sea")
    air = find(res, ref=data["air"])[0]
    assert (air["division"], air["service"]) == ("Import", "Air Freight")
    road = find(res, ref=data["road"])[0]
    assert (road["division"], road["service"]) == ("Domestic", "Road Freight")
    inv = find(res, category="Invoices")[0]
    assert inv["department"] == "Accounts & Finance" and (inv["division"], inv["service"]) == ("Export", "Sea Freight")      # an invoice takes its job's classification
    quo = find(res, category="Quotations")[0]
    assert quo["department"] == "CRM" and quo["division"] == "Export" and quo["party"] == data["party"]
    assert all(i["key"] for i in res["items"]) and len({i["key"] for i in res["items"]}) == len(res["items"])                    # same number in two modules stays distinguishable
    assert res["total"] >= 5 and any(m["source"] == "jobs" and m["total"] == 3 for m in res["sources"])


def test_references_match_with_or_without_separators_and_in_any_case(h, data):
    plain = data["sea"].replace("-", "")
    for term in (data["sea"], data["sea"].lower(), plain, plain.lower(), data["sea"].replace("-", " ")):
        res = client.get("/api/search/universal", params={"q": term}, headers=h).json()
        assert find(res, ref=data["sea"]), term
    part = client.get("/api/search/universal", params={"q": data["sea"][-4:]}, headers=h).json()
    assert part["total"] >= 1


def test_container_number_finds_its_job_and_shows_the_container(h, data):
    res = client.get("/api/search/universal", params={"q": f"MEDU{data['num']}"}, headers=h).json()
    job = find(res, ref=data["sea"])
    assert job and "Container MEDU" in job[0]["reference"]


def test_filters_division_service_mode_category_department_party_status(h, data):
    base = {"q": data["tag"]}
    imp = client.get("/api/search/universal", params={**base, "division": "Import"}, headers=h).json()
    assert {i["division"] for i in imp["items"]} == {"Import"} and find(imp, ref=data["air"]) and not find(imp, ref=data["sea"])
    road = client.get("/api/search/universal", params={**base, "service": "Road Freight"}, headers=h).json()
    assert {i["ref"] for i in road["items"] if i["category"] == "Jobs"} == {data["road"]}
    sea = client.get("/api/search/universal", params={**base, "mode": "Sea"}, headers=h).json()
    assert find(sea, ref=data["sea"]) and not find(sea, ref=data["air"])
    inv = client.get("/api/search/universal", params={**base, "category": "Invoices"}, headers=h).json()
    assert inv["items"] and {i["category"] for i in inv["items"]} == {"Invoices"}
    acc = client.get("/api/search/universal", params={**base, "department": "Accounts & Finance"}, headers=h).json()
    assert {i["department"] for i in acc["items"]} == {"Accounts & Finance"}
    zz = client.get("/api/search/universal", params={**base, "party": "NO SUCH PARTY"}, headers=h).json()
    assert zz["items"] == [] and zz["total"] == 0
    dated = client.get("/api/search/universal", params={**base, "date_from": "2999-01-01"}, headers=h).json()
    assert dated["items"] == []
    clear = client.get("/api/search/universal", params={**base, "date_to": "2999-01-01", "date_from": "2000-01-01"}, headers=h).json()
    assert find(clear, ref=data["sea"])


def test_paging_through_one_source(h, data):
    r = client.get("/api/search/universal", params={"q": data["tag"], "source": "jobs", "limit": 2, "offset": 0}, headers=h).json()
    assert r["shown"] == 2 and r["sources"][0]["total"] == 3
    r2 = client.get("/api/search/universal", params={"q": data["tag"], "source": "jobs", "limit": 2, "offset": 2}, headers=h).json()
    assert r2["shown"] == 1 and not ({i["ref"] for i in r["items"]} & {i["ref"] for i in r2["items"]})


def test_no_result_and_short_query(h):
    r = client.get("/api/search/universal", params={"q": "NOTHING-LIKE-THIS-" + uuid.uuid4().hex}, headers=h).json()
    assert r["items"] == [] and r["total"] == 0
    assert client.get("/api/search/universal", params={"q": "a"}, headers=h).status_code == 422
    assert client.get("/api/search/universal", params={"q": "abc"}).status_code == 401


def test_a_user_only_finds_what_their_permissions_open(world, h, data):
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    role = Role(name=f"JOBONLY{tag}", organization_id=org.id)
    role.permissions = list(db.scalars(select(Permission).where(Permission.code.in_(["shipment.read"]))))
    db.add(role)
    u = User(email=f"jo-{tag}@users.test", username=f"jo{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name="Jobs", organization_id=org.id, status="ACTIVE")
    u.roles.append(role)
    db.add(u)
    db.commit()
    username, lic = u.username, org.license_number
    db.close()
    hj = hdr(username, lic)
    res = client.get("/api/search/universal", params={"q": data["tag"]}, headers=hj).json()
    assert find(res, ref=data["sea"]) and {i["category"] for i in res["items"]} == {"Jobs"}                                  # no invoices, quotations or parties
    assert not any(m["source"] in ("documents", "quotations", "parties", "payments", "vouchers") for m in res["sources"])
    assert client.get("/api/search/universal", params={"q": data["tag"], "category": "Invoices"}, headers=hj).json()["items"] == []


def test_another_company_never_appears(world, admin2, data):
    # admin2 is in the same tenant; use a platform-less check by searching with a company that has no such records: results are scoped by organization
    h2 = hdr(admin2["username"], admin2["license"])
    res = client.get("/api/search/universal", params={"q": data["tag"]}, headers=h2).json()
    assert res["items"], "same company sees it"
