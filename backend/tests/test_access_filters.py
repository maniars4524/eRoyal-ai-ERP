"""Placed users only see the customers and jobs their role allows: lists, direct ids and universal search. Unplaced users keep full access."""
import uuid

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Organization, Permission, Role, Shipment, User
from app.security import hash_password
from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def make_user(world, label):
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    role = Role(name=f"AF{label}{tag}", organization_id=org.id)
    role.permissions = list(db.scalars(select(Permission).where(Permission.code.in_(["shipment.read", "shipment.update", "customer.read", "customer.update", "master.read"]))))
    db.add(role)
    u = User(email=f"{label}-{tag}@users.test", username=f"{label}{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name=label, organization_id=org.id, status="ACTIVE")
    u.roles.append(role)
    db.add(u)
    db.commit()
    out = {"id": u.id, "username": u.username, "license": org.license_number}
    db.close()
    return out


@pytest.fixture(scope="module")
def world2(world, h):
    tag = uuid.uuid4().hex[:5].upper()
    b1 = client.post("/api/access/branches", json={"code": f"F1{tag}", "name": f"Alpha {tag}", "region": "R1"}, headers=h).json()
    b2 = client.post("/api/access/branches", json={"code": f"F2{tag}", "name": f"Beta {tag}", "region": "R1"}, headers=h).json()
    a, b, bm, plain = (make_user(world, x) for x in ("sala", "salb", "bmgr", "plain"))
    put = lambda u, body: client.put(f"/api/access/users/{u['id']}", json=body, headers=h)
    assert put(a, {"user_category": "SALESPERSON", "branch_id": b1["id"], "salesperson_name": f"Anil{tag}"}).status_code == 200
    assert put(b, {"user_category": "SALESPERSON", "branch_id": b1["id"], "salesperson_name": f"Bala{tag}"}).status_code == 200
    assert put(bm, {"user_category": "BRANCH_MANAGER", "branch_id": b1["id"]}).status_code == 200
    ca = client.post("/api/customers", json={"code": f"CA{tag}", "name": f"CustA {tag}"}, headers=h).json()
    cb = client.post("/api/customers", json={"code": f"CB{tag}", "name": f"CustB {tag}"}, headers=h).json()
    cc = client.post("/api/customers", json={"code": f"CC{tag}", "name": f"CustC {tag}"}, headers=h).json()
    assert client.put(f"/api/access/customers/{ca['id']}/assignments", json={"user_ids": [a["id"]]}, headers=h).status_code == 200
    assert client.put(f"/api/access/customers/{cb['id']}/assignments", json={"user_ids": [b["id"]]}, headers=h).status_code == 200
    ja = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR", "consignee": f"ZQ{tag} A", "customer_id": ca["id"]}, headers=h).json()
    jb = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR", "consignee": f"ZQ{tag} B", "customer_id": cb["id"]}, headers=h).json()
    jc = client.post("/api/shipments", json={"mode": "Air", "direction": "Import", "pol": "SIN", "pod": "INMAA4", "consignee": f"ZQ{tag} C", "customer_id": cc["id"]}, headers=h).json()
    jd = client.post("/api/shipments", json={"mode": "Road", "direction": "Domestic", "pol": "Chennai", "pod": "Bangalore", "consignee": f"ZQ{tag} D"}, headers=h).json()
    db = SessionLocal()
    db.execute(update(Shipment).where(Shipment.id == jc["id"]).values(salesperson=f"Anil{tag}"))     # sold under A's name, customer not assigned
    db.execute(update(Shipment).where(Shipment.id == jd["id"]).values(branch=f"Alpha {tag}"))          # handled by branch 1
    db.commit()
    db.close()
    return {"tag": tag, "a": a, "b": b, "bm": bm, "plain": plain, "ca": ca, "cb": cb, "cc": cc, "ja": ja, "jb": jb, "jc": jc, "jd": jd}


def hu(u):
    return hdr(u["username"], u["license"])


def jobs_seen(u):
    return {j["job_number"] for j in client.get("/api/shipments", headers=hu(u)).json()}


def test_an_unplaced_user_sees_everything_as_before(world2):
    seen = jobs_seen(world2["plain"])
    assert {world2[k]["job_number"] for k in ("ja", "jb", "jc", "jd")} <= seen
    assert {c["id"] for c in client.get("/api/customers", headers=hu(world2["plain"])).json()} >= {world2[k]["id"] for k in ("ca", "cb", "cc")}


def test_salesperson_sees_only_own_customers_and_jobs(world2):
    a = world2["a"]
    seen = jobs_seen(a)
    assert world2["ja"]["job_number"] in seen and world2["jc"]["job_number"] in seen          # assigned customer + sold under A's name
    assert world2["jb"]["job_number"] not in seen and world2["jd"]["job_number"] not in seen   # B's customer, branch job of nobody's
    cust = {c["id"] for c in client.get("/api/customers", headers=hu(a)).json()}
    assert world2["ca"]["id"] in cust and world2["cb"]["id"] not in cust and world2["cc"]["id"] not in cust


def test_direct_ids_of_other_peoples_records_are_not_found(world2):
    a = world2["a"]
    assert client.get(f"/api/shipments/{world2['jb']['id']}", headers=hu(a)).status_code == 404
    assert client.get(f"/api/shipments/{world2['ja']['id']}", headers=hu(a)).status_code == 200
    assert client.get(f"/api/customers/{world2['cb']['id']}", headers=hu(a)).status_code == 404
    assert client.get(f"/api/customers/{world2['ca']['id']}", headers=hu(a)).status_code == 200
    assert client.patch(f"/api/shipments/{world2['jb']['id']}", json={"commodity": "x"}, headers=hu(a)).status_code == 404
    assert client.patch(f"/api/customers/{world2['cb']['id']}", json={"name": "hijack"}, headers=hu(a)).status_code == 404


def test_two_salespersons_do_not_see_each_other(world2):
    b = world2["b"]
    seen = jobs_seen(b)
    assert seen == {world2["jb"]["job_number"]}


def test_branch_manager_sees_the_branch_jobs(world2):
    seen = jobs_seen(world2["bm"])
    assert world2["jd"]["job_number"] in seen                       # the job handled by their branch
    assert world2["ja"]["job_number"] not in seen and world2["jb"]["job_number"] not in seen


def test_universal_search_respects_the_same_rules(world2):
    a = world2["a"]
    res = client.get("/api/search/universal", params={"q": f"ZQ{world2['tag']}"}, headers=hu(a)).json()
    refs = {i["ref"] for i in res["items"] if i["category"] == "Jobs"}
    assert refs == {world2["ja"]["job_number"], world2["jc"]["job_number"]}
    parties = client.get("/api/search/universal", params={"q": f"Cust", "category": "Customers"}, headers=hu(a)).json()
    names = " ".join(i["title"] if "title" in i else str(i) for i in parties["items"])
    assert f"CustB {world2['tag']}" not in names
    full = client.get("/api/search/universal", params={"q": f"ZQ{world2['tag']}"}, headers=hu(world2["plain"])).json()
    assert {i["ref"] for i in full["items"] if i["category"] == "Jobs"} == {world2[k]["job_number"] for k in ("ja", "jb", "jc", "jd")}
