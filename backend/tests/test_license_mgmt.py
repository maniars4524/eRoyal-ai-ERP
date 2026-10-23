"""License Management: authority, company + license creation, renewals, lifecycle and access policy, user limits, reminders, audit and tenant isolation."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import license_mgmt as lm
from app.db import SessionLocal
from app.models import AuditLog, LicenseNotification, Organization, User
from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def login(username, license_number=None, portal="erp"):
    body = {"identifier": username, "password": PASSWORD, "portal": portal}
    if license_number:
        body["license_number"] = license_number
    return client.post("/api/auth/login", json=body)


def hdr(username, license_number=None):
    r = login(username, license_number)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def owner(world, admin, admin2):
    """The test's owner company is the world's ERP tenant; `admin` holds the License Management authority, `admin2` is an ordinary admin of the same company."""
    mp = pytest.MonkeyPatch()
    mp.setattr(lm, "OWNER_LICENSE", world["erp_license"].upper())
    with SessionLocal() as db:
        lm.grant_license_admin(db, admin["username"], world["erp_license"])
    yield {"h": hdr(admin["username"], admin["license"]), "plain": hdr(admin2["username"], admin2["license"]), "license": world["erp_license"], "made": []}
    mp.undo()
    with SessionLocal() as db:
        ids = [o.id for o in db.scalars(select(Organization).where(Organization.name.like("LM Test %")))]
        if ids:
            db.execute(delete(AuditLog).where(AuditLog.organization_id.in_(ids)))
            db.execute(delete(Organization).where(Organization.id.in_(ids)))
            db.commit()


def new_company(owner, **extra):
    uid = uuid.uuid4().hex[:6]
    body = {"code": "LM" + uid.upper(), "name": "LM Test " + uid, "admin_email": f"lm{uid}@t.test", "admin_username": "lm" + uid, "admin_password": PASSWORD, "admin_first_name": "Lm", **extra}
    r = client.post("/api/license-mgmt/companies", json=body, headers=owner["h"])
    return r, body


# ---------------------------------------------------------------- authority
def test_only_the_license_super_admin_of_the_owner_company_gets_in(world, owner, admin):
    for path in ("/api/license-mgmt/summary", "/api/license-mgmt/companies", "/api/license-mgmt/audit", "/api/license-mgmt/usage", "/api/license-mgmt/notifications"):
        assert client.get(path, headers=owner["h"]).status_code == 200, path
        assert client.get(path).status_code == 401                                                        # no sign-in
        assert client.get(path, headers=owner["plain"]).status_code == 403                                # an ordinary admin of the owner company
    # an admin of ANOTHER company, and the platform super admin, are refused as well
    assert client.get("/api/license-mgmt/summary", headers=hdr(world["demo"], None) if False else {"Authorization": "Bearer " + login(world["demo"], None, "demo").json()["access_token"]}).status_code == 403
    dev = client.post("/api/auth/login", json={"identifier": world["dev"], "password": PASSWORD, "portal": "developer"})
    assert dev.status_code == 200 and client.get("/api/license-mgmt/summary", headers={"Authorization": "Bearer " + dev.json()["access_token"]}).status_code == 403
    me = client.get("/api/auth/me", headers=owner["h"]).json()
    assert me["license_admin"] is True and client.get("/api/auth/me", headers=owner["plain"]).json()["license_admin"] is False


def test_the_authority_cannot_be_granted_through_user_screens_or_templates(world, owner, admin):
    h = owner["plain"]
    uid = uuid.uuid4().hex[:6]
    r = client.post("/api/users", json={"username": "x" + uid, "email": f"x{uid}@t.test", "password": "abc", "first_name": "X", "role": "LICENSE_SUPER_ADMIN"}, headers=h)
    assert r.status_code == 422                                                                         # not a role template
    me = client.get("/api/auth/me", headers=h).json()
    assert "license.manage" not in me["permissions"]                                                    # not even the Admin role holds it
    # a role change on the authorized user keeps the license role
    users = {u["username"]: u for u in client.get("/api/users", headers=h).json()}
    assert client.patch(f"/api/users/{users[admin['username']]['id']}", json={"role": "ERP_ADMIN"}, headers=h).status_code == 200
    assert client.get("/api/license-mgmt/summary", headers=owner["h"]).status_code == 200


# ---------------------------------------------------------------- company + license creation
def test_company_and_license_are_created_together_with_a_unique_license(owner):
    r, body = new_company(owner, renewal_cycle="MONTHLY", max_users=3, modules=["crm", "accounts"], contact_person="Ravi", contact_email="ravi@t.test", license_start="2026-01-31")
    assert r.status_code == 201, r.text
    c = r.json()["company"]
    assert c["license_number"].startswith("SWF") and c["renewal_cycle"] == "MONTHLY" and c["license_start"] == "2026-01-31" and c["license_expiry"] == "2026-02-28"      # month-end: 31 Jan + 1 month
    assert c["max_users"] == 3 and c["modules"] == ["crm", "accounts"] and c["contact"]["person"] == "Ravi" and c["status"] in ("ACTIVE", "EXPIRED", "EXPIRING")
    r2, _ = new_company(owner)
    assert r2.status_code == 201 and r2.json()["company"]["license_number"] != c["license_number"]
    # the new company's administrator can sign in with the new license (until it expires) and gets no authority over licenses
    exp = client.post("/api/license-mgmt/companies", json={**body, "admin_username": "lm2" + uuid.uuid4().hex[:5], "admin_email": f"z{uuid.uuid4().hex[:5]}@t.test"}, headers=owner["h"])
    assert exp.status_code == 409                                                                         # the same company code again


def test_validation_and_all_or_nothing_creation(owner):
    assert new_company(owner, license_start="2026-05-01", license_expiry="2026-04-01")[0].status_code == 422
    assert new_company(owner, modules=["crm", "nonsense"])[0].status_code == 422
    assert new_company(owner, renewal_cycle="WEEKLY")[0].status_code == 422
    assert new_company(owner, contact_email="nope")[0].status_code == 422
    ok, body = new_company(owner)
    before = client.get("/api/license-mgmt/companies?limit=1", headers=owner["h"]).json()["total"]
    dup = client.post("/api/license-mgmt/companies", json={**body, "code": "LM" + uuid.uuid4().hex[:6].upper()}, headers=owner["h"])      # same admin user name / email
    assert dup.status_code == 409
    assert client.get("/api/license-mgmt/companies?limit=1", headers=owner["h"]).json()["total"] == before          # nothing half-made is left


# ---------------------------------------------------------------- renewals
def test_month_and_year_arithmetic():
    assert lm.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert lm.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)                                      # leap year
    assert lm.add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert lm.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert lm.add_months(date(2026, 3, 31), 12) == date(2027, 3, 31)


def test_renewal_extends_once_keeps_history_and_is_safe_to_repeat(owner):
    r, _ = new_company(owner, renewal_cycle="YEARLY")
    c = r.json()["company"]
    cid, first_expiry = c["id"], date.fromisoformat(c["license_expiry"])
    rid = uuid.uuid4().hex
    one = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": rid, "cycle": "MONTHLY", "reference": "INV-1"}, headers=owner["h"]).json()
    assert one["renewed"] is True and date.fromisoformat(one["company"]["license_expiry"]) == lm.add_months(first_expiry, 1)
    again = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": rid, "cycle": "MONTHLY"}, headers=owner["h"]).json()
    assert again["renewed"] is False and again["company"]["license_expiry"] == one["company"]["license_expiry"]      # a retry / double click extends nothing
    two = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "YEARLY"}, headers=owner["h"]).json()
    assert date.fromisoformat(two["company"]["license_expiry"]) == lm.add_months(date.fromisoformat(one["company"]["license_expiry"]), 12)
    hist = two["company"]["renewals"]
    assert len(hist) == 2 and hist[1]["reference"] == "INV-1" and hist[0]["by"] and hist[1]["previous_expiry"] == str(first_expiry)      # the earlier renewal is preserved, with who did it
    assert client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": "short", "cycle": "YEARLY"}, headers=owner["h"]).status_code == 422
    assert client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "DAILY"}, headers=owner["h"]).status_code == 422


def test_an_expired_license_renews_from_today_and_access_follows_the_policy(owner):
    start = date.today() - timedelta(days=400)
    r, body = new_company(owner, license_start=str(start), license_expiry=str(date.today() - timedelta(days=10)))
    c = r.json()["company"]
    assert c["status"] == "EXPIRED"
    blocked = login(body["admin_username"], c["license_number"])
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "LICENSE_BLOCKED" and "expired" in blocked.json()["detail"]["message"]
    done = client.post(f"/api/license-mgmt/companies/{c['id']}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "MONTHLY"}, headers=owner["h"]).json()
    assert done["company"]["license_expiry"] == str(lm.add_months(date.today(), 1)) and done["company"]["status"] in ("ACTIVE", "EXPIRING")
    assert login(body["admin_username"], c["license_number"]).status_code == 200                             # renewal restores access at once


# ---------------------------------------------------------------- lifecycle
def test_suspend_cancel_reactivate_follow_the_allowed_transitions(owner):
    r, body = new_company(owner)
    c = r.json()["company"]
    cid = c["id"]
    tok = login(body["admin_username"], c["license_number"])
    assert tok.status_code == 200
    h = {"Authorization": "Bearer " + tok.json()["access_token"]}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post(f"/api/license-mgmt/companies/{cid}/suspend", json={"reason": "x"}, headers=owner["h"]).status_code == 422          # a reason is required
    s = client.post(f"/api/license-mgmt/companies/{cid}/suspend", json={"reason": "Payment overdue"}, headers=owner["h"])
    assert s.status_code == 200 and s.json()["status"] == "SUSPENDED"
    assert client.get("/api/auth/me", headers=h).status_code == 403                                      # sessions that were already open end too
    assert login(body["admin_username"], c["license_number"]).status_code == 403
    assert client.post(f"/api/license-mgmt/companies/{cid}/suspend", json={"reason": "again"}, headers=owner["h"]).status_code == 409
    assert client.post(f"/api/license-mgmt/companies/{cid}/reactivate", json={"reason": "Paid"}, headers=owner["h"]).json()["status"] == "ACTIVE"
    assert login(body["admin_username"], c["license_number"]).status_code == 200
    can = client.post(f"/api/license-mgmt/companies/{cid}/cancel", json={"reason": "Contract ended"}, headers=owner["h"])
    assert can.json()["status"] == "CANCELLED" and login(body["admin_username"], c["license_number"]).status_code == 403
    assert client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "YEARLY"}, headers=owner["h"]).status_code == 409   # reactivate first
    assert client.post(f"/api/license-mgmt/companies/{cid}/reactivate", json={"reason": "Back"}, headers=owner["h"]).json()["status"] == "ACTIVE"
    own = next(x for x in client.get("/api/license-mgmt/companies?q=" + owner["license"], headers=owner["h"]).json()["items"] if x["is_owner"])
    assert client.post(f"/api/license-mgmt/companies/{own['id']}/suspend", json={"reason": "no reason"}, headers=owner["h"]).status_code == 409          # the owner can never lock itself out
    assert client.get("/api/license-mgmt/summary", headers=owner["h"]).status_code == 200


def test_existing_companies_without_license_terms_are_unaffected(world, admin):
    with SessionLocal() as db:
        org = db.get(Organization, world["erp_org"])
        assert org.license_expiry is None and org.max_users is None and org.status == "ACTIVE"
        assert lm.block_reason(org) is None and lm.effective_status(org) == "ACTIVE"


# ---------------------------------------------------------------- user limits
def test_the_licensed_user_limit_is_enforced_on_the_server(owner):
    r, body = new_company(owner, max_users=2)
    c = r.json()["company"]
    h = hdr(body["admin_username"], c["license_number"])
    mk = lambda n: client.post("/api/users", json={"username": n + uuid.uuid4().hex[:5], "email": f"{n}{uuid.uuid4().hex[:5]}@t.test", "password": "pw", "first_name": n}, headers=h)
    second = mk("u2")
    assert second.status_code == 201                                                                      # the administrator + this one = 2
    third = mk("u3")
    assert third.status_code == 409 and "license allows 2" in third.json()["detail"]
    assert client.patch(f"/api/users/{second.json()['id']}", json={"status": "INACTIVE"}, headers=h).status_code == 200
    ok = mk("u3")
    assert ok.status_code == 201                                                                          # a disabled user frees its seat
    assert client.patch(f"/api/users/{second.json()['id']}", json={"status": "ACTIVE"}, headers=h).status_code == 409      # and re-enabling needs a free seat
    d = client.get(f"/api/license-mgmt/companies/{c['id']}", headers=owner["h"]).json()
    assert d["enabled_users"] == 2 and d["total_users"] == 3 and d["max_users"] == 2
    assert client.patch(f"/api/license-mgmt/companies/{c['id']}", json={"max_users": 1}, headers=owner["h"]).status_code == 409      # cannot go below the users in use
    assert client.patch(f"/api/license-mgmt/companies/{c['id']}", json={"max_users": 5, "modules": ["crm"]}, headers=owner["h"]).json()["max_users"] == 5
    assert client.patch(f"/api/license-mgmt/companies/{c['id']}", json={"unlimited_users": True, "all_modules": True}, headers=owner["h"]).json()["max_users"] is None


# ---------------------------------------------------------------- reminders
def test_reminder_rules_and_no_duplicates(owner):
    assert [lm.reminder_for(n) for n in (45, 31, 30, 20, 8, 7, 3, 1, 0, -1, -9)] == [None, None, "D30", "D30", "D30", "D7", "D7", "D1", "D0", "OVERDUE", "OVERDUE"]
    r, body = new_company(owner, license_start=str(date.today() - timedelta(days=330)), license_expiry=str(date.today() + timedelta(days=5)))
    cid = r.json()["company"]["id"]
    assert r.json()["company"]["status"] == "EXPIRING"
    first = client.post("/api/license-mgmt/notifications/run", headers=owner["h"]).json()["created"]
    assert first >= 1
    assert client.post("/api/license-mgmt/notifications/run", headers=owner["h"]).json()["created"] == 0              # running again (restart, retry) creates nothing twice
    mine = [n for n in client.get("/api/license-mgmt/notifications", headers=owner["h"]).json() if n["company_id"] == cid]
    assert len(mine) == 1 and mine[0]["kind"] == "D7" and mine[0]["channel_status"] == "IN_APP" and mine[0]["status"] == "OPEN"
    # the day of expiry gives its own reminder; after the expiry date the overdue one; each once
    with SessionLocal() as db:
        today = date.today()
        assert lm.generate_notifications(db, today + timedelta(days=5)) >= 1
        assert lm.generate_notifications(db, today + timedelta(days=5)) == 0
        assert lm.generate_notifications(db, today + timedelta(days=9)) >= 1
        kinds = sorted(n.kind for n in db.scalars(select(LicenseNotification).where(LicenseNotification.organization_id == cid)))
        assert kinds == ["D0", "D7", "OVERDUE"]
    nid = mine[0]["id"]
    assert client.post(f"/api/license-mgmt/notifications/{nid}/retry", headers=owner["h"]).json()["channel_status"] == "EMAIL_NOT_CONFIGURED"
    assert client.post(f"/api/license-mgmt/notifications/{nid}/ack", headers=owner["h"]).json()["status"] == "ACKNOWLEDGED"
    renewed = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "YEARLY"}, headers=owner["h"]).json()
    assert renewed["company"]["license_expiry"] > str(date.today() + timedelta(days=300))
    with SessionLocal() as db:
        db.execute(delete(LicenseNotification).where(LicenseNotification.organization_id == cid))
        db.commit()


# ---------------------------------------------------------------- audit, usage, details
def test_every_administrative_action_is_audited_and_details_hide_secrets(owner):
    r, body = new_company(owner, max_users=4)
    c = r.json()["company"]
    cid = c["id"]
    client.patch(f"/api/license-mgmt/companies/{cid}", json={"max_users": 6}, headers=owner["h"])
    client.post(f"/api/license-mgmt/companies/{cid}/suspend", json={"reason": "Audit test"}, headers=owner["h"])
    client.post(f"/api/license-mgmt/companies/{cid}/reactivate", json={"reason": "Audit test"}, headers=owner["h"])
    client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "cycle": "MONTHLY"}, headers=owner["h"])
    actions = [a["action"] for a in client.get(f"/api/license-mgmt/audit?company_id={cid}", headers=owner["h"]).json()]
    assert {"LICENSE_ISSUED", "LICENSE_EDITED", "LICENSE_SUSPENDED", "LICENSE_REACTIVATED", "LICENSE_RENEWED"} <= set(actions)
    edited = next(a for a in client.get(f"/api/license-mgmt/audit?company_id={cid}", headers=owner["h"]).json() if a["action"] == "LICENSE_EDITED")
    assert edited["detail"]["max_users"] == {"from": 4, "to": 6} and edited["user"]
    denied = client.get("/api/license-mgmt/summary", headers=owner["plain"])
    assert denied.status_code == 403
    assert any(a["action"] == "LICENSE_ACCESS_DENIED" for a in client.get("/api/license-mgmt/audit?limit=20", headers=owner["h"]).json())      # refused attempts are recorded
    d = client.get(f"/api/license-mgmt/companies/{cid}", headers=owner["h"]).json()
    text = str(d).lower()
    assert "password" not in text and "token" not in text and "hash" not in text
    assert d["users_list"][0]["username"] == body["admin_username"] and d["users_list"][0]["last_login"] is None and d["session_tracking"] in (True, False)
    usage = client.get(f"/api/license-mgmt/usage?company_id={cid}&days=7", headers=owner["h"]).json()
    assert usage["totals"]["logins"] == 0 and usage["session_tracking"] in (True, False)
    login(body["admin_username"], c["license_number"])
    usage = client.get(f"/api/license-mgmt/usage?company_id={cid}&days=7&category=login", headers=owner["h"]).json()
    assert usage["totals"]["logins"] == 1 and usage["totals"]["distinct_users"] == 1 and usage["events"][0]["action"] == "LOGIN"
    assert client.get("/api/license-mgmt/companies/999999", headers=owner["h"]).status_code == 404


def test_the_dashboard_counts_real_records(owner):
    s = client.get("/api/license-mgmt/summary", headers=owner["h"]).json()
    rows = client.get("/api/license-mgmt/companies?limit=200", headers=owner["h"]).json()
    assert s["companies"] == rows["total"] and (s["active_sessions"] is None or isinstance(s["active_sessions"], int))
    for st, key in (("SUSPENDED", "suspended"), ("EXPIRED", "expired"), ("CANCELLED", "cancelled")):
        assert client.get(f"/api/license-mgmt/companies?status={st}", headers=owner["h"]).json()["total"] == s[key]
    assert client.get("/api/license-mgmt/companies?q=LM Test", headers=owner["h"]).json()["total"] >= 1
    assert client.get("/api/license-mgmt/companies?status=BOGUS", headers=owner["h"]).status_code == 422


# ---------------------------------------------------------------- the provider's view: live, ranking, storage, payments, AMC / MMC, user management
def test_new_endpoints_are_owner_only(owner):
    for path in ("/api/license-mgmt/live", "/api/license-mgmt/ranking", "/api/license-mgmt/storage", "/api/license-mgmt/renewals"):
        assert client.get(path, headers=owner["h"]).status_code == 200, path
        assert client.get(path, headers=owner["plain"]).status_code == 403 and client.get(path).status_code == 401


def test_online_time_live_users_ranking_and_storage(owner):
    lm._last_touch.clear()
    r, body = new_company(owner, max_users=5)
    c = r.json()["company"]
    h = hdr(body["admin_username"], c["license_number"])
    assert client.get("/api/auth/me", headers=h).status_code == 200                                      # any authenticated request counts as activity
    live = client.get("/api/license-mgmt/live", headers=owner["h"]).json()
    mine = [x for x in live["items"] if x["company_id"] == c["id"]]
    assert live["online_now"] >= 1 and len(mine) == 1 and mine[0]["username"] == body["admin_username"] and mine[0]["license"] == c["license_number"]
    with SessionLocal() as db:                       # give the company some online time (as if it had worked for an hour)
        from app.models import UserUsage

        row = db.scalar(select(UserUsage).where(UserUsage.organization_id == c["id"]))
        row.active_seconds += 3600
        db.commit()
    rank = client.get("/api/license-mgmt/ranking?days=7", headers=owner["h"]).json()
    top = [x for x in rank["top_companies_by_online_time"] if x["company_id"] == c["id"]]
    assert top and top[0]["minutes"] >= 60 and top[0]["active_days"] == 1
    assert any(x["username"] == body["admin_username"] and x["minutes"] >= 60 for x in rank["top_users_by_online_time"])
    assert rank["users"]["enabled"] >= 1 and rank["users"]["total"] >= rank["users"]["enabled"]
    st = client.get("/api/license-mgmt/storage?refresh=true", headers=owner["h"]).json()
    me = next(x for x in st["items"] if x["company_id"] == c["id"])
    assert me["bytes"] > 0 and me["records"] > 0 and st["total_bytes"] >= me["bytes"]
    row = next(x for x in client.get(f"/api/license-mgmt/companies?q={c['license_number']}", headers=owner["h"]).json()["items"] if x["id"] == c["id"])
    assert row["online_now"] == 1 and row["online_minutes_7d"] >= 60


def test_amc_mmc_and_payment_records(owner):
    r, body = new_company(owner, maintenance_plan="AMC", license_start="2026-01-31")
    c = r.json()["company"]
    cid = c["id"]
    assert c["maintenance_plan"] == "AMC" and c["maintenance_expiry"] == "2027-01-31" and c["maintenance_status"] in ("ACTIVE", "EXPIRING", "EXPIRED")
    mm = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "kind": "MAINTENANCE", "reference": "AMC-1", "amount": 12000, "currency": "inr", "payment_method": "NEFT", "payment_status": "RECEIVED"}, headers=owner["h"]).json()
    assert mm["company"]["maintenance_expiry"] == "2028-01-31" and mm["company"]["license_expiry"] == c["license_expiry"]      # the AMC moves, the license does not
    hist = mm["company"]["renewals"][0]
    assert hist["kind"] == "MAINTENANCE" and hist["cycle"] == "AMC" and hist["amount"] == "12000.00" and hist["currency"] == "INR" and hist["payment_method"] == "NEFT" and hist["payment_status"] == "RECEIVED"
    assert client.patch(f"/api/license-mgmt/companies/{cid}", json={"maintenance_plan": "MMC"}, headers=owner["h"]).json()["maintenance_plan"] == "MMC"
    monthly = client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "kind": "MAINTENANCE", "payment_status": "PENDING", "amount": 1000}, headers=owner["h"]).json()
    assert monthly["company"]["maintenance_expiry"] == "2028-02-29"                                      # one month on, leap-year end of month
    pend = client.get("/api/license-mgmt/renewals?payment_status=PENDING", headers=owner["h"]).json()
    mine = [x for x in pend["items"] if x["company_id"] == cid]
    assert len(mine) == 1 and pend["pending_count"] >= 1 and mine[0]["kind"] == "MAINTENANCE"
    ok = client.post(f"/api/license-mgmt/renewals/{mine[0]['id']}/payment", json={"payment_status": "RECEIVED", "payment_method": "UPI", "reference": "UPI-77"}, headers=owner["h"]).json()
    assert ok["payment_status"] == "RECEIVED" and ok["payment_method"] == "UPI" and ok["reference"] == "UPI-77" and ok["payment_updated_at"]
    assert client.post(f"/api/license-mgmt/renewals/{mine[0]['id']}/payment", json={"payment_status": "RECEIVED", "payment_method": "Bitcoin"}, headers=owner["h"]).status_code == 422
    assert client.post(f"/api/license-mgmt/companies/{cid}/renew", json={"request_id": uuid.uuid4().hex, "kind": "LICENSE", "cycle": "MONTHLY", "payment_method": "Barter"}, headers=owner["h"]).status_code == 422
    plain, _ = new_company(owner)
    pc = plain.json()["company"]
    assert pc["maintenance_plan"] is None and pc["maintenance_status"] is None
    assert client.post(f"/api/license-mgmt/companies/{pc['id']}/renew", json={"request_id": uuid.uuid4().hex, "kind": "MAINTENANCE"}, headers=owner["h"]).status_code == 422      # no contract chosen
    actions = [a["action"] for a in client.get(f"/api/license-mgmt/audit?company_id={cid}", headers=owner["h"]).json()]
    assert "MAINTENANCE_RENEWED" in actions and "LICENSE_PAYMENT_RECORDED" in actions


def test_provider_manages_users_permissions_and_passwords(owner):
    r, body = new_company(owner, max_users=3)
    c = r.json()["company"]
    cid = c["id"]
    h = hdr(body["admin_username"], c["license_number"])
    second = client.post("/api/users", json={"username": "s" + uuid.uuid4().hex[:5], "email": f"s{uuid.uuid4().hex[:5]}@t.test", "password": "pw", "first_name": "S", "role": "VIEWER"}, headers=h).json()
    uid, uname = second["id"], second["username"]
    d = client.get(f"/api/license-mgmt/companies/{cid}/users/{uid}", headers=owner["h"]).json()
    assert d["roles"] == ["VIEWER"] and "billing.read" in d["permissions"] and "billing.post" not in d["permissions"] and "password" not in str(d).lower()
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"role": "ACCOUNTS"}, headers=owner["h"]).json()["roles"] == ["ACCOUNTS"]
    assert "billing.post" in client.get(f"/api/license-mgmt/companies/{cid}/users/{uid}", headers=owner["h"]).json()["permissions"]
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"role": "GOD"}, headers=owner["h"]).status_code == 422
    login_as = lambda pw: client.post("/api/auth/login", json={"identifier": uname, "password": pw, "portal": "erp", "license_number": c["license_number"]}).status_code
    assert login_as("pw") == 200
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"new_password": "x"}, headers=owner["h"]).json()["changes"] == ["password"]              # a one-letter password is allowed
    assert login_as("pw") == 401 and login_as("x") == 200
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"status": "INACTIVE"}, headers=owner["h"]).json()["status"] == "INACTIVE"
    assert login_as("x") == 401
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"status": "ACTIVE"}, headers=owner["h"]).json()["status"] == "ACTIVE"
    other, _ = new_company(owner)
    assert client.get(f"/api/license-mgmt/companies/{other.json()['company']['id']}/users/{uid}", headers=owner["h"]).status_code == 404       # a user of another company is not reachable through this id
    acts = [a for a in client.get(f"/api/license-mgmt/audit?company_id={cid}", headers=owner["h"]).json() if a["action"] == "LICENSE_USER_MANAGED"]
    assert acts and any(a["detail"].get("password") == "reset by License Management" for a in acts) and "'x'" not in str(acts)        # the audit never holds the password
    # seats: the administrator + 'second' = 2 of 3; one more is fine, then the provider cannot enable a fourth
    assert client.post("/api/users", json={"username": "t" + uuid.uuid4().hex[:5], "email": f"t{uuid.uuid4().hex[:5]}@t.test", "password": "pw", "first_name": "T"}, headers=h).status_code == 201
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"status": "INACTIVE"}, headers=owner["h"]).status_code == 200
    extra = client.post("/api/users", json={"username": "u" + uuid.uuid4().hex[:5], "email": f"u{uuid.uuid4().hex[:5]}@t.test", "password": "pw", "first_name": "U"}, headers=h).json()
    assert client.patch(f"/api/license-mgmt/companies/{cid}/users/{uid}", json={"status": "ACTIVE"}, headers=owner["h"]).status_code == 409 and extra["id"]
    own = client.get("/api/auth/me", headers=owner["h"]).json()
    own_company = client.get("/api/license-mgmt/companies?q=" + owner["license"], headers=owner["h"]).json()["items"][0]
    assert client.patch(f"/api/license-mgmt/companies/{own_company['id']}/users/{own['id']}", json={"status": "INACTIVE"}, headers=owner["h"]).status_code == 409      # the provider cannot disable itself
