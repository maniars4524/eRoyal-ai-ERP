import uuid
from datetime import date, timedelta

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
def h2(world, admin2):
    return hdr(admin2["username"], admin2["license"])


def party(h, credit=True, **data):
    tag = uuid.uuid4().hex[:6].upper()
    body = {"code": "C" + tag, "name": "Party " + tag, "roles": ["shipper"], "data": {"credit_customer": "yes" if credit else "no", **data}}
    r = client.post("/api/customers", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def facility(h, h2, cid, limit="100000", days=30, start=None, expiry=None, policy="SOFT", branch_limits=None, approve=True):
    f = client.post(f"/api/customers/{cid}/credit/facilities", json={"kind": "NEW", "credit_days": days, "requested_limit": limit, "currency": "INR", "agreement_no": "AGR-1", "branch_limits": branch_limits or []}, headers=h)
    assert f.status_code == 201, f.text
    fid = f.json()["id"]
    assert client.post(f"/api/customers/{cid}/credit/facilities/{fid}/submit", headers=h).status_code == 200
    if not approve:
        return fid
    body = {"approved_limit": limit, "start_date": str(start or date.today()), "expiry_date": str(expiry or date.today() + timedelta(days=365)), "policy": policy, "remarks": "ok"}
    if branch_limits:
        body["branch_limits"] = branch_limits
    r = client.post(f"/api/customers/{cid}/credit/facilities/{fid}/approve", json=body, headers=h2)
    assert r.status_code == 200, r.text
    return fid


def job(h, cid, revenue=0, branch=None):
    body = {"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "BEANR", "customer_id": cid, "revenue": revenue}
    if branch:
        body["party_branch_id"] = branch
    return client.post("/api/shipments", json=body, headers=h)


def test_branches_billing_flag_and_isolation(h, admin2, world):
    c = party(h, credit=False)
    base = {"code": "maa", "name": "Chennai", "state": "Tamil Nadu", "country": "India", "city": "Chennai", "address": "1 Mount Rd", "pincode": "600002", "gstin": "33AAACT2727Q1ZN", "billing_enabled": True}
    b = client.post(f"/api/customers/{c['id']}/branches", json=base, headers=h)
    assert b.status_code == 201 and b.json()["code"] == "MAA" and b.json()["billing_enabled"] is True
    assert client.post(f"/api/customers/{c['id']}/branches", json=base, headers=h).status_code == 409  # duplicate code
    nobill = client.post(f"/api/customers/{c['id']}/branches", json={"code": "CJB", "name": "Coimbatore", "billing_enabled": False}, headers=h)
    assert nobill.status_code == 201 and nobill.json()["billing_enabled"] is False
    assert client.post(f"/api/customers/{c['id']}/branches", json={"code": "DEL", "name": "Delhi", "billing_enabled": True}, headers=h).status_code == 422  # billing needs address + tax id
    assert len(client.get(f"/api/customers/{c['id']}/branches", headers=h).json()) == 2
    off = client.patch(f"/api/customers/{c['id']}/branches/{nobill.json()['id']}", json={"status": "INACTIVE"}, headers=h)
    assert off.json()["status"] == "INACTIVE"
    # a second company cannot see or touch it
    other = party_other_tenant(world)
    assert client.get(f"/api/customers/{c['id']}/branches", headers=other).status_code == 404
    assert client.post(f"/api/customers/{c['id']}/branches", json=base, headers=other).status_code == 404


def party_other_tenant(world):
    return hdr(world["demo"], portal="demo")  # a different company (the demo tenant)


def test_contacts_are_branch_wise(h):
    c = party(h, credit=False)
    b1 = client.post(f"/api/customers/{c['id']}/branches", json={"code": "B1", "name": "One"}, headers=h).json()
    b2 = client.post(f"/api/customers/{c['id']}/branches", json={"code": "B2", "name": "Two"}, headers=h).json()
    for name, br, billing in (("Ops", b1["id"], False), ("Accounts", b1["id"], True), ("Mgr", b2["id"], False)):
        r = client.post(f"/api/customers/{c['id']}/contacts", json={"branch_id": br, "name": name, "department": "Finance" if billing else "Operations", "is_billing": billing, "is_primary": name == "Ops", "email": f"{name.lower()}@x.test"}, headers=h)
        assert r.status_code == 201, r.text
    assert {x["name"] for x in client.get(f"/api/customers/{c['id']}/contacts?branch_id={b1['id']}", headers=h).json()} == {"Ops", "Accounts"}
    assert [x["name"] for x in client.get(f"/api/customers/{c['id']}/contacts?billing=true", headers=h).json()] == ["Accounts"]
    other_party = party(h, credit=False)
    foreign_branch = client.post(f"/api/customers/{other_party['id']}/branches", json={"code": "Z", "name": "Z"}, headers=h).json()
    assert client.post(f"/api/customers/{c['id']}/contacts", json={"branch_id": foreign_branch["id"], "name": "Bad"}, headers=h).status_code == 422
    assert client.post(f"/api/customers/{c['id']}/contacts", json={"name": "Org-level contact"}, headers=h).status_code == 201  # org-level contacts stay valid


def test_accounting_classification_validates_control_accounts(h):
    tag = uuid.uuid4().hex[:5].upper()
    body = {"code": "A" + tag, "name": "Acct " + tag, "roles": ["shipper"], "data": {"acct_class": "customer", "ar_account": "NOPE"}}
    assert client.post("/api/customers", json=body, headers=h).status_code == 422
    body["data"] = {"acct_class": "customer"}
    assert client.post("/api/customers", json=body, headers=h).status_code == 422  # AR control account required
    client.post("/api/masters/account", json={"code": "AR" + tag, "name": "Sundry Debtors", "data": {}}, headers=h)
    client.post("/api/masters/account", json={"code": "AP" + tag, "name": "Sundry Creditors", "data": {}}, headers=h)
    body["code"] = "B" + tag
    body["data"] = {"acct_class": "both", "ar_account": "AR" + tag, "ap_account": "AP" + tag}
    r = client.post("/api/customers", json=body, headers=h)
    assert r.status_code == 201 and r.json()["data"]["acct_class"] == "both"
    assert client.patch(f"/api/customers/{r.json()['id']}", json={"data": {"acct_class": "vendor", "ap_account": "MISSING"}}, headers=h).status_code == 422


def test_credit_application_workflow_and_segregation(h, h2):
    c = party(h)
    f = client.post(f"/api/customers/{c['id']}/credit/facilities", json={"kind": "NEW", "credit_days": 30, "requested_limit": "500000", "currency": "INR"}, headers=h).json()
    assert f["status"] == "DRAFT"
    assert client.post(f"/api/customers/{c['id']}/credit/facilities", json={"kind": "NEW", "requested_limit": "1"}, headers=h).status_code == 409  # one open application
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/submit", headers=h).status_code == 422  # agreement number missing
    assert client.patch(f"/api/customers/{c['id']}/credit/facilities/{f['id']}", json={"agreement_no": "AGR-9"}, headers=h).status_code == 200
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/submit", headers=h).json()["status"] == "SUBMITTED"
    body = {"approved_limit": "400000", "expiry_date": str(date.today() + timedelta(days=365)), "policy": "SOFT"}
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/approve", json=body, headers=h).status_code == 403  # own application
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/review", headers=h2).json()["status"] == "UNDER_REVIEW"
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/approve", json={"approved_limit": "400000"}, headers=h2).status_code == 422  # expiry required
    ok = client.post(f"/api/customers/{c['id']}/credit/facilities/{f['id']}/approve", json=body, headers=h2).json()
    assert ok["status"] == "APPROVED" and ok["approved_limit"] == "400000.00" and ok["approved_by"]
    assert client.patch(f"/api/customers/{c['id']}/credit/facilities/{f['id']}", json={"requested_limit": "1"}, headers=h).status_code == 409  # approved = immutable
    summary = client.get(f"/api/customers/{c['id']}/credit", headers=h).json()
    assert summary["status"]["state"] == "OK" and summary["status"]["available"] == "400000.00"
    actions = [e["action"] for e in summary["events"]]
    assert {"APPLICATION_CREATED", "APPLICATION_SUBMITTED", "APPLICATION_UNDER_REVIEW", "APPLICATION_APPROVED"} <= set(actions)
    # non-credit customers cannot apply
    n = party(h, credit=False)
    assert client.post(f"/api/customers/{n['id']}/credit/facilities", json={"kind": "NEW", "requested_limit": "10"}, headers=h).status_code == 409
    # rejection needs a reason
    d = party(h)
    fid = facility(h, h2, d["id"], approve=False)
    assert client.post(f"/api/customers/{d['id']}/credit/facilities/{fid}/reject", json={}, headers=h2).status_code == 422
    assert client.post(f"/api/customers/{d['id']}/credit/facilities/{fid}/reject", json={"remarks": "Weak financials"}, headers=h2).json()["status"] == "REJECTED"


def test_soft_block_needs_authorised_override_and_one_time_is_consumed(h, h2):
    c = party(h)
    facility(h, h2, c["id"], limit="100000", policy="SOFT")
    assert job(h, c["id"], 60000).status_code == 201
    blocked = job(h, c["id"], 150000)
    assert blocked.status_code == 409 and "CREDIT_BLOCK" in blocked.json()["detail"] and "Soft block" in blocked.json()["detail"]
    req = client.post(f"/api/customers/{c['id']}/credit/requests", json={"kind": "OVERRIDE", "reason": "Key account shipment", "one_time": True}, headers=h).json()
    assert client.post(f"/api/customers/{c['id']}/credit/requests/{req['id']}/approve", json={}, headers=h).status_code == 403  # no self-approval
    assert job(h, c["id"], 150000).status_code == 409  # still blocked while pending
    assert client.post(f"/api/customers/{c['id']}/credit/requests/{req['id']}/approve", json={}, headers=h2).json()["status"] == "APPROVED"
    assert job(h, c["id"], 150000).status_code == 201
    assert job(h, c["id"], 150000).status_code == 409  # one-time override used up


def test_hard_block_ignores_overrides_and_needs_a_release(h, h2):
    c = party(h)
    facility(h, h2, c["id"], limit="50000", policy="HARD")
    assert job(h, c["id"], 80000).status_code == 409
    assert client.post(f"/api/customers/{c['id']}/credit/requests", json={"kind": "OVERRIDE", "reason": "please"}, headers=h).status_code == 409
    rel = client.post(f"/api/customers/{c['id']}/credit/requests", json={"kind": "RELEASE", "reason": "Management release"}, headers=h).json()
    assert client.post(f"/api/customers/{c['id']}/credit/requests/{rel['id']}/approve", json={}, headers=h2).status_code == 422  # validity period required
    ok = client.post(f"/api/customers/{c['id']}/credit/requests/{rel['id']}/approve", json={"valid_until": str(date.today() + timedelta(days=2))}, headers=h2)
    assert ok.status_code == 200
    assert job(h, c["id"], 80000).status_code == 201


def test_extension_and_grace_do_not_silently_raise_the_limit(h, h2):
    c = party(h)
    facility(h, h2, c["id"], limit="100000", policy="HARD")
    assert job(h, c["id"], 120000).status_code == 409
    ext = client.post(f"/api/customers/{c['id']}/credit/requests", json={"kind": "EXTENSION", "amount": "50000", "reason": "Peak season", "valid_until": str(date.today() + timedelta(days=30))}, headers=h).json()
    assert job(h, c["id"], 120000).status_code == 409  # not effective until approved
    client.post(f"/api/customers/{c['id']}/credit/requests/{ext['id']}/approve", json={}, headers=h2)
    assert job(h, c["id"], 120000).status_code == 201
    s = client.get(f"/api/customers/{c['id']}/credit", headers=h).json()["status"]
    assert s["limit"] == "100000.00" and s["effective_limit"] == "150000.00"  # original facility preserved
    g = party(h)
    facility(h, h2, g["id"], limit="100000", policy="HARD")
    assert job(h, g["id"], 130000).status_code == 409
    gr = client.post(f"/api/customers/{g['id']}/credit/requests", json={"kind": "GRACE", "days": 5, "amount": "40000", "reason": "Payment in transit"}, headers=h).json()
    client.post(f"/api/customers/{g['id']}/credit/requests/{gr['id']}/approve", json={"amount": "40000"}, headers=h2)
    assert job(h, g["id"], 130000).status_code == 201  # within the separately authorised grace allowance
    assert job(h, g["id"], 500000).status_code == 409  # beyond it


def test_expired_facility_blocks_until_renewed_and_history_is_kept(h, h2):
    c = party(h)
    fid = facility(h, h2, c["id"], limit="100000", start=date.today() - timedelta(days=60), expiry=date.today() - timedelta(days=1), policy="HARD")
    assert client.get(f"/api/customers/{c['id']}/credit", headers=h).json()["status"]["state"] == "EXPIRED"
    assert job(h, c["id"], 0).status_code == 409
    r = client.post(f"/api/customers/{c['id']}/credit/facilities", json={"kind": "RENEWAL", "credit_days": 45, "requested_limit": "200000", "currency": "INR", "agreement_no": "AGR-2", "reason": "Annual renewal"}, headers=h)
    assert r.status_code == 201 and r.json()["prev_id"] == fid
    nid = r.json()["id"]
    client.post(f"/api/customers/{c['id']}/credit/facilities/{nid}/submit", headers=h)
    assert client.post(f"/api/customers/{c['id']}/credit/facilities/{nid}/approve", json={"approved_limit": "200000", "expiry_date": str(date.today() + timedelta(days=365)), "policy": "HARD"}, headers=h2).status_code == 200
    s = client.get(f"/api/customers/{c['id']}/credit", headers=h).json()
    assert s["status"]["state"] == "OK" and {f["id"]: f["status"] for f in s["facilities"]} == {fid: "SUPERSEDED", nid: "APPROVED"}
    assert job(h, c["id"], 10).status_code == 201
    alerts = client.get("/api/credit/overview", headers=h).json()
    assert any(x["customer_id"] == c["id"] for x in alerts["customers"])


def test_branch_sub_limits_are_validated(h, h2):
    c = party(h)
    ids = [client.post(f"/api/customers/{c['id']}/branches", json={"code": code, "name": code}, headers=h).json()["id"] for code in ("CH", "MU")]
    bad = client.post(f"/api/customers/{c['id']}/credit/facilities", json={"kind": "NEW", "requested_limit": "100000", "branch_limits": [{"branch_id": ids[0], "limit": "70000"}, {"branch_id": ids[1], "limit": "50000"}]}, headers=h)
    assert bad.status_code == 422  # sub-limits exceed the parent limit
    facility(h, h2, c["id"], limit="100000", policy="HARD", branch_limits=[{"branch_id": ids[0], "limit": "30000"}, {"branch_id": ids[1], "limit": "70000"}])
    assert job(h, c["id"], 20000, branch=ids[0]).status_code == 201
    over = job(h, c["id"], 20000, branch=ids[0])
    assert over.status_code == 409 and "sub-limit" in over.json()["detail"]
    assert job(h, c["id"], 20000, branch=ids[1]).status_code == 201  # other branch still has room
    foreign = party(h, credit=False)
    fb = client.post(f"/api/customers/{foreign['id']}/branches", json={"code": "ZZ", "name": "ZZ"}, headers=h).json()["id"]
    assert job(h, c["id"], 0, branch=fb).status_code == 422  # branch of another organization


def test_invoice_billing_branch_and_credit_at_posting(h, h2):
    c = party(h)
    nb = client.post(f"/api/customers/{c['id']}/branches", json={"code": "NB", "name": "No billing", "billing_enabled": False}, headers=h).json()
    bb = client.post(f"/api/customers/{c['id']}/branches", json={"code": "BB", "name": "Billing", "address": "x", "city": "y", "gstin": "33AAACT2727Q1ZN", "billing_enabled": True}, headers=h).json()
    doc = lambda br, rate: client.post("/api/documents", json={"doc_type": "invoice", "customer_id": c["id"], "party_name": c["name"], "header": {"party_branch_id": br}, "lines": [{"charge_name": "x", "rate": rate}]}, headers=h).json()
    d1 = doc(nb["id"], "1000")
    r = client.post(f"/api/documents/{d1['id']}/post", headers=h)
    assert r.status_code == 422 and "not billing-enabled" in r.json()["detail"]
    d2 = doc(bb["id"], "1000")
    assert client.post(f"/api/documents/{d2['id']}/post", headers=h).status_code == 200
    facility(h, h2, c["id"], limit="5000", policy="HARD")
    d3 = doc(bb["id"], "9000")
    blocked = client.post(f"/api/documents/{d3['id']}/post", headers=h)
    assert blocked.status_code == 409 and "CREDIT_BLOCK" in blocked.json()["detail"]


def test_non_credit_and_unapproved_customers_are_not_restricted(h):
    n = party(h, credit=False)
    assert job(h, n["id"], 10_000_000).status_code == 201
    u = party(h)  # credit customer, no approved facility yet: advance-payment process
    assert job(h, u["id"], 10_000_000).status_code == 201
    assert client.get(f"/api/customers/{u['id']}/credit", headers=h).json()["status"]["state"] == "NO_FACILITY"


def test_credit_permissions_are_enforced(h, admin):
    viewer_name = "vw" + uuid.uuid4().hex[:6]
    assert client.post("/api/users", json={"username": viewer_name, "email": viewer_name + "@t.test", "password": PASSWORD, "first_name": "V", "role": "VIEWER"}, headers=h).status_code in (200, 201)
    v = hdr(viewer_name, admin["license"])
    c = party(h)
    assert client.get(f"/api/customers/{c['id']}/credit", headers=v).status_code == 200
    assert client.post(f"/api/customers/{c['id']}/credit/facilities", json={"kind": "NEW", "requested_limit": "1"}, headers=v).status_code == 403
    assert client.patch(f"/api/customers/{c['id']}", json={"data": {"credit_customer": "no"}}, headers=v).status_code == 403
