"""Role-based access foundation: company branches, user placement, reporting line scope, customer assignments, audit branch, CEO permission."""
import uuid

import pytest
from sqlalchemy import select

from app.access import scope_for
from app.db import SessionLocal
from app.models import AuditLog, Organization, Permission, Role, User
from app.roles import ensure_role
from app.security import hash_password
from tests.test_bookings import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def make_user(world, label):
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    role = Role(name=f"ACC{label}{tag}", organization_id=org.id)
    role.permissions = list(db.scalars(select(Permission).where(Permission.code.in_(["shipment.read", "customer.read"]))))
    db.add(role)
    u = User(email=f"{label}-{tag}@users.test", username=f"{label}{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name=label, organization_id=org.id, status="ACTIVE")
    u.roles.append(role)
    db.add(u)
    db.commit()
    out = {"id": u.id, "username": u.username, "license": org.license_number}
    db.close()
    return out


@pytest.fixture(scope="module")
def people(world, h):
    tag = uuid.uuid4().hex[:4].upper()
    b1 = client.post("/api/access/branches", json={"code": f"CH{tag}", "name": f"Chennai {tag}", "region": f"South{tag}"}, headers=h).json()
    b2 = client.post("/api/access/branches", json={"code": f"BL{tag}", "name": f"Bengaluru {tag}", "region": f"South{tag}"}, headers=h).json()
    b3 = client.post("/api/access/branches", json={"code": f"DL{tag}", "name": f"Delhi {tag}", "region": f"North{tag}"}, headers=h).json()
    mgr, sp1, sp2, bm, nh = (make_user(world, x) for x in ("mgr", "spa", "spb", "bmg", "nhd"))
    put = lambda u, body: client.put(f"/api/access/users/{u['id']}", json=body, headers=h)
    assert put(mgr, {"user_category": "SALES_MANAGER", "branch_id": b1["id"]}).status_code == 200
    assert put(sp1, {"user_category": "SALESPERSON", "branch_id": b1["id"], "reporting_manager_id": mgr["id"], "salesperson_name": "Anil"}).status_code == 200
    assert put(sp2, {"user_category": "SALESPERSON", "branch_id": b2["id"]}).status_code == 200
    assert put(bm, {"user_category": "BRANCH_MANAGER", "branch_id": b1["id"]}).status_code == 200
    assert put(nh, {"user_category": "NATIONAL_HEAD", "region": f"South{tag}", "branch_id": b3["id"]}).status_code == 200
    return {"b1": b1, "b2": b2, "b3": b3, "mgr": mgr, "sp1": sp1, "sp2": sp2, "bm": bm, "nh": nh}


def scope(uid):
    db = SessionLocal()
    try:
        return scope_for(db, db.get(User, uid))
    finally:
        db.close()


def test_branch_master_create_update_duplicate_and_deactivate(h, people):
    b = people["b1"]
    assert client.post("/api/access/branches", json={"code": b["code"], "name": "dup"}, headers=h).status_code == 409
    upd = client.put(f"/api/access/branches/{b['id']}", json={**{k: b[k] for k in ("code", "name", "region", "city", "state", "is_head_office")}, "status": "INACTIVE"}, headers=h)
    assert upd.status_code == 200 and upd.json()["status"] == "INACTIVE"
    assert client.put(f"/api/access/branches/{b['id']}", json={**{k: b[k] for k in ("code", "name", "region", "city", "state", "is_head_office")}, "status": "ACTIVE"}, headers=h).status_code == 200
    assert any(x["id"] == b["id"] for x in client.get("/api/access/branches", headers=h).json())


def test_an_unplaced_user_keeps_unrestricted_access(world):
    u = make_user(world, "legacy")
    s = scope(u["id"])
    assert s.unrestricted and s.branch_ids is None and s.user_ids is None


def test_salesperson_sees_only_themselves_and_their_branch(people):
    s = scope(people["sp1"]["id"])
    assert not s.unrestricted and s.user_ids == {people["sp1"]["id"]} and s.branch_ids == {people["b1"]["id"]}
    assert not s.allows_user(people["sp2"]["id"]) and not s.allows_branch(people["b2"]["id"])
    assert s.salesperson_names == {"Anil"}


def test_manager_sees_the_team_below_but_not_other_teams(people):
    s = scope(people["mgr"]["id"])
    assert s.user_ids == {people["mgr"]["id"], people["sp1"]["id"]} and not s.allows_user(people["sp2"]["id"])


def test_branch_manager_sees_their_branch_only(people):
    s = scope(people["bm"]["id"])
    assert s.user_ids is None and s.branch_ids == {people["b1"]["id"]}


def test_national_head_gets_region_plus_given_branches(people):
    s = scope(people["nh"]["id"])
    assert s.branch_ids == {people["b1"]["id"], people["b2"]["id"], people["b3"]["id"]}


def test_reporting_loop_and_foreign_branch_are_refused(h, people):
    r = client.put(f"/api/access/users/{people['mgr']['id']}", json={"user_category": "SALES_MANAGER", "branch_id": people["b1"]["id"], "reporting_manager_id": people["sp1"]["id"]}, headers=h)
    assert r.status_code == 422 and "loop" in r.text
    assert client.put(f"/api/access/users/{people['sp1']['id']}", json={"user_category": "SALESPERSON", "branch_id": 999999}, headers=h).status_code == 422
    assert client.put(f"/api/access/users/{people['sp1']['id']}", json={"user_category": "SALESPERSON"}, headers=h).status_code == 422     # placed but no branch = would see nothing
    assert client.put(f"/api/access/users/{people['sp1']['id']}", json={"dashboards": ["CEO"]}, headers=h).status_code == 422


def test_setting_access_needs_the_permission_and_is_audited(world, h, people):
    plain = make_user(world, "plain")
    hp = hdr(plain["username"], plain["license"])
    assert client.put(f"/api/access/users/{people['sp1']['id']}", json={"user_category": "SALESPERSON", "branch_id": people["b1"]["id"]}, headers=hp).status_code == 403
    assert client.post("/api/access/branches", json={"code": "ZZ1", "name": "x"}, headers=hp).status_code == 403
    db = SessionLocal()
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "USER_ACCESS_CHANGED", AuditLog.entity_id == str(people["sp1"]["id"]))))
    db.close()
    assert rows and rows[-1].old_value is not None and rows[-1].new_value["user_category"] == "SALESPERSON"


def test_my_access_reports_scope_and_dashboards(people):
    sp = people["sp1"]
    me = client.get("/api/access/me", headers=hdr(sp["username"], sp["license"])).json()
    assert me["scope"]["category"] == "SALESPERSON" and me["dashboards"] == ["EXISTING"] and me["ceo_dashboard"] is False


def test_ceo_dashboard_permission_is_not_in_any_template_role(world, admin, h):
    me = client.get("/api/access/me", headers=h).json()
    assert me["ceo_dashboard"] is False and "CEO" not in me["dashboards"]          # even a company admin does not get it automatically
    db = SessionLocal()
    role = ensure_role(db, world["erp_org"], "ERP_ADMIN")
    assert "dashboard.ceo" not in {p.code for p in role.permissions}
    db.close()


def test_customer_assignment_roundtrip_and_company_check(world, h, people):
    c = client.post("/api/customers", json={"name": "Assign Co " + uuid.uuid4().hex[:5], "code": "AS" + uuid.uuid4().hex[:5].upper()}, headers=h)
    assert c.status_code in (200, 201), c.text
    cid = c.json()["id"]
    r = client.put(f"/api/access/customers/{cid}/assignments", json={"user_ids": [people["sp1"]["id"]]}, headers=h)
    assert r.status_code == 200 and r.json()["user_ids"] == [people["sp1"]["id"]]
    assert client.get(f"/api/access/customers/{cid}/assignments", headers=h).json()["user_ids"] == [people["sp1"]["id"]]
    assert client.put(f"/api/access/customers/{cid}/assignments", json={"user_ids": [999999]}, headers=h).status_code == 422
    assert client.put(f"/api/access/customers/{cid}/assignments", json={"user_ids": []}, headers=h).json()["user_ids"] == []
