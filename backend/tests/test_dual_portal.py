"""A company flagged for both login pages (ERP and Demo): its users sign in on either, get the right session, and the demo limits apply only to a Demo session."""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.licensing import next_license_number
from app.main import app
from app.models import Organization, User
from app.roles import ensure_role
from app.security import hash_password

client = TestClient(app)


def make_company(demo: bool, both: bool):
    uid = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        org = Organization(code="DP" + uid.upper(), license_number=next_license_number(db), name="Dual " + uid, status="ACTIVE", is_demo=demo, demo_portal=both)
        db.add(org)
        db.flush()
        u = User(organization_id=org.id, username="dp" + uid, email=f"dp{uid}@t.test", password_hash=hash_password("dp" + uid), first_name="Dp", status="ACTIVE", profile={})
        u.roles.append(ensure_role(db, org.id, "ERP_ADMIN"))
        db.add(u)
        db.commit()
        return org.license_number, "dp" + uid


def login(user, pw, portal, lic=None):
    body = {"identifier": user, "password": pw, "portal": portal}
    if lic is not None:
        body["license_number"] = lic
    return client.post("/api/auth/login", json=body)


def test_demo_company_flagged_for_both_pages_can_use_the_erp_page_with_full_rights(world):
    lic, user = make_company(demo=True, both=True)
    demo = login(user, user, "demo")
    assert demo.status_code == 200 and demo.json()["user"]["portal"] == "demo"
    erp = login(user, user, "erp", lic)
    assert erp.status_code == 200 and erp.json()["user"]["portal"] == "erp"
    assert login(user, user, "erp", "WRONG").status_code == 401                        # the ERP page still needs the company license
    h_demo, h_erp = ({"Authorization": "Bearer " + r.json()["access_token"]} for r in (demo, erp))
    new = lambda name: {"username": name, "email": name + "@t.test", "password": "x", "first_name": "N"}
    assert client.post("/api/users", json=new("a" + user), headers=h_demo).status_code == 403    # a Demo session keeps the demo limits
    assert client.post("/api/users", json=new("b" + user), headers=h_erp).status_code == 201     # an ERP session of the same account has full rights
    assert client.get("/api/auth/me", headers=h_erp).json()["portal"] == "erp" and client.get("/api/auth/me", headers=h_demo).json()["portal"] == "demo"


def test_unflagged_companies_keep_their_one_page(world):
    lic, user = make_company(demo=True, both=False)
    assert login(user, user, "demo").status_code == 200
    assert login(user, user, "erp", lic).status_code == 403
    lic2, user2 = make_company(demo=False, both=False)
    assert login(user2, user2, "erp", lic2).status_code == 200 and login(user2, user2, "demo").status_code in (401, 403)


def test_demo_page_license_is_shown_and_checked(world):
    lic, user = make_company(demo=True, both=True)
    info = client.get("/api/auth/portal-info?portal=demo").json()
    assert info["license"] and info["license"].startswith("SWF")
    assert login(user, user, "demo", lic).status_code == 200
    assert login(user, user, "demo", "SWF99999").status_code == 401                    # a license typed on the Demo page must be the account's own
    assert login(user, user, "demo", "").status_code == 200                            # and may be left empty, as before
    assert client.get("/api/auth/portal-info?portal=erp").json() == {"license": None}
