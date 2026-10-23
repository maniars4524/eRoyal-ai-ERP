import threading
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app import sequences as sq

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
def lic(admin):
    return admin["license"]


def mkuser(h, lic, role, branch=None):
    name = "sq" + uuid.uuid4().hex[:7]
    body = {"username": name, "email": name + "@t.test", "password": PASSWORD, "first_name": "S", "role": role}
    if branch:
        body["profile"] = {"branch": branch}
    assert client.post("/api/users", json=body, headers=h).status_code in (200, 201)
    return hdr(name, lic)


def comps(*items):
    out = []
    for it in items:
        out.append({"k": it} if it in ("branch", "shipment", "doctype", "fy", "cyear", "cyear2", "number") else ({"k": "sep", "v": it[1:]} if it.startswith("~") else {"k": "prefix", "v": it}))
    return out


def create(h, doc_type, name, comp, **kw):
    body = {"name": name, "doc_type": doc_type, "components": comp, **kw}
    r = client.post("/api/sequences", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def activate(h, cid, expect=200):
    r = client.post(f"/api/sequences/{cid}/activate", json={}, headers=h)
    assert r.status_code == expect, r.text
    return r


def seqs(h, **params):
    return client.get("/api/sequences", params={"limit": 500, **params}, headers=h).json()["items"]


def deactivate_all(h, doc_type):
    for c in seqs(h):
        if c["doc_type"] == doc_type and c["status"] == "ACTIVE":
            assert client.post(f"/api/sequences/{c['id']}/deactivate", json={"reason": "test reset"}, headers=h).status_code == 200


def post_invoice(h, party="Seq Co"):
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": party, "lines": [{"charge_name": "x", "rate": "100"}]}, headers=h).json()
    r = client.post(f"/api/documents/{d['id']}/post", headers=h)
    return r


def test_defaults_reproduce_the_legacy_formats_and_the_screen_lists_them(h):
    items = seqs(h)
    docs = {c["doc_type"]: c for c in items}
    assert {"job", "quotation", "booking", "invoice", "receipt", "payment", "lead", "enquiry"} <= set(docs)
    assert all(c["is_system_default"] and c["status"] == "ACTIVE" for c in docs.values() if c["doc_type"] in ("invoice", "quotation"))
    r = post_invoice(h)
    assert r.status_code == 200 and r.json()["doc_number"].startswith("INV/") and len(r.json()["doc_number"]) <= 16
    j = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export"}, headers=h).json()["job_number"]
    assert j.startswith("SEAEXP-") and j.endswith("-000001") or j.startswith("SEAEXP-")


def test_invoice_and_voucher_numbers_never_exceed_16_characters(h):
    # a too-long invoice format is refused on the backend with the actual length
    long = create(h, "invoice", "Too long", comps("SWAY", "~/", "branch", "~/", "doctype", "~/", "fy", "~/", "number"), number_width=6)
    r = activate(h, long["id"], 422)
    assert "exceeds the maximum permitted length of 16 characters" in str(r.json()) and "Current length" in str(r.json())
    prev = client.post("/api/sequences/preview", json={"name": "xx", "doc_type": "invoice", "components": comps("SWAY", "~/", "doctype", "~/", "fy", "~/", "number"), "number_width": 6}, headers=h).json()
    assert prev["validation"]["ok"] is False and prev["validation"]["worst_length"] == 4 + 1 + 3 + 1 + 5 + 1 + 6
    # the configured maximum itself cannot be raised above 16 for financial documents
    assert client.post("/api/sequences", json={"name": "Raise", "doc_type": "payment", "components": comps("PV", "~-", "number"), "max_length": 30}, headers=h).status_code == 201
    r = client.post("/api/sequences/preview", json={"name": "Raise", "doc_type": "payment", "components": comps("PV", "~-", "number"), "max_length": 30}, headers=h).json()
    assert any("maximum identifier length" in e for e in r["validation"]["errors"])
    # non-financial documents are not held to 16
    ok = create(h, "enquiry", "Long enquiry", comps("LOGISTICSLTD", "~/", "ENQ", "~/", "fy", "~/", "number"), number_width=6)
    assert client.post(f"/api/sequences/{ok['id']}/test", headers=h).json()["validation"]["ok"] is True


def test_format_validation_rules(h):
    pv = lambda comp, **kw: client.post("/api/sequences/preview", json={"name": "tt", "doc_type": "quotation", "components": comp, **kw}, headers=h).json()["validation"]
    assert not pv([{"k": "prefix", "v": "QT"}])["ok"]  # needs the number
    assert not pv(comps("QT", "number", "number"))["ok"]  # only one number
    assert not pv([{"k": "prefix", "v": "Q T!"}, {"k": "number"}])["ok"]
    assert not pv([{"k": "prefix", "v": "QT"}, {"k": "sep", "v": "#"}, {"k": "number"}])["ok"]
    assert not pv(comps("QT", "~/", "number"), branch_scope="NOPE")["ok"]
    assert not pv(comps("QT", "~/", "shipment", "~/", "number"))["ok"]  # quotations do not depend on shipment type
    assert pv(comps("QT", "~/", "fy", "~/", "number"))["ok"]


def test_preview_and_test_do_not_consume_numbers(h):
    c = create(h, "debit_note", "Preview only", comps("DN", "~-", "number"), number_width=4, start_number=500)
    activate(h, c["id"])
    before = seqs(h, q="Preview only")[0]["next_number"]
    for _ in range(3):
        client.post("/api/sequences/preview", json={"name": "pp", "doc_type": "debit_note", "components": comps("DN", "~-", "number"), "number_width": 4, "start_number": 500}, headers=h)
        t = client.post(f"/api/sequences/{c['id']}/test", headers=h).json()
        assert t["consumed"] is False and t["samples"][0]["number"] == "DN-0500"
    assert seqs(h, q="Preview only")[0]["next_number"] == before == "DN-0500"
    client.post(f"/api/sequences/{c['id']}/deactivate", json={"reason": "cleanup"}, headers=h)


def test_activation_conflicts_versions_and_the_integration_with_real_invoices(h):
    deactivate_all(h, "invoice")
    missing = post_invoice(h, "No seq")
    assert missing.status_code == 409 and "No active" in missing.json()["detail"] and "no number was issued" in missing.json()["detail"]
    a = create(h, "invoice", "Invoice v1", comps("INV", "~-", "number"), number_width=6, start_number=1)
    activate(h, a["id"])
    n1 = post_invoice(h).json()["doc_number"]
    n2 = post_invoice(h).json()["doc_number"]
    assert (n1, n2) == ("INV-000001", "INV-000002")
    b = create(h, "invoice", "Invoice duplicate scope", comps("INV", "~-", "number"), number_width=6)
    clash = activate(h, b["id"], 409)
    assert "already covers the same scope" in clash.json()["detail"]
    assert any(e["action"] == "CONFLICT_DETECTED" for e in client.get(f"/api/sequences/{b['id']}/history", headers=h).json()["events"])
    # an active sequence is not edited in place; a new version replaces it and keeps the old one for history
    assert client.patch(f"/api/sequences/{a['id']}", json={"number_width": 8}, headers=h).status_code == 409
    v2 = client.post(f"/api/sequences/{a['id']}/new-version", headers=h).json()
    assert v2["version"] == 2 and v2["status"] == "DRAFT" and v2["replaces_id"] == a["id"]
    client.patch(f"/api/sequences/{v2['id']}", json={"components": comps("TX", "~-", "number"), "start_number": 100}, headers=h)
    rep = activate(h, v2["id"]).json()
    assert rep["replaced"] and next(c for c in seqs(h) if c["id"] == a["id"])["status"] == "RETIRED"
    assert post_invoice(h).json()["doc_number"] == "TX-000100"
    hist = client.get(f"/api/sequences/{a['id']}/history", headers=h).json()
    assert hist["issued_total"] == 2 and [x["number"] for x in hist["issued"]] == ["INV-000002", "INV-000001"]  # issued numbers stay traceable to the old version
    client.post(f"/api/sequences/{b['id']}/retire", json={"reason": "unused"}, headers=h)


def test_scope_resolution_branch_shipment_and_ambiguity(h, lic):
    for code in ("MAA", "BOM"):
        client.post("/api/masters/branch", json={"code": code, "name": "Branch " + code, "data": {}}, headers=h)
    deactivate_all(h, "booking")
    general = create(h, "booking", "Booking all", comps("BKG", "~/", "number"), number_width=4)
    activate(h, general["id"])
    chennai = create(h, "booking", "Booking Chennai", comps("MAA", "~/", "BK", "~/", "number"), number_width=4, branch_scope="MAA")
    activate(h, chennai["id"])
    maa_user = mkuser(h, lic, "ERP_ADMIN", branch="Branch MAA")
    other = mkuser(h, lic, "ERP_ADMIN")
    def book(u):
        from datetime import timedelta
        body = {"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Acme Exports", "carrier": "MSC", "carrier_booking_no": "MSC123", "vessel": "MSC ROMA / 254W", "pol": "INMAA", "pod": "BEANR",
                "etd": str(date.today() + timedelta(days=10)), "eta": str(date.today() + timedelta(days=30)), "si_cutoff": str(date.today() + timedelta(days=1)), "vgm_cutoff": str(date.today() + timedelta(days=2)),
                "data": {"pcs": "120", "gwt": "24000", "consignee": "Solvay SA", "marks": "Soda ash", "charges": [{"type": "AP", "code": "OFR", "qty": "2", "rate": "680", "currency": "USD"}]}}
        created = client.post("/api/bookings", json=body, headers=u)        # the number is issued when the booking is first saved
        if created.status_code != 201:
            return created
        r = client.post(f"/api/bookings/{created.json()['id']}/confirm", headers=u)
        return r
    r1 = book(maa_user)
    assert r1.status_code == 200 and r1.json()["number"].startswith("MAA/BK/")  # most specific (branch) sequence wins for that user's branch
    r2 = book(other)
    assert r2.status_code == 200 and r2.json()["number"].startswith("BKG/")  # everyone else uses the general one
    # two equally specific active sequences = refuse to number at all
    db = SessionLocal()
    try:
        from app.models import SequenceConfig
        g = db.get(SequenceConfig, general["id"])
        dup = SequenceConfig(organization_id=g.organization_id, name="forced duplicate", module=g.module, doc_type="booking", components=g.components, number_width=4, start_number=1, step=1, reset_policy="FY", max_length=40, fy_format="YY-YY", shipment_codes={}, status="ACTIVE", version=1)
        db.add(dup)
        db.commit()
        dup_id = dup.id
    finally:
        db.close()
    r3 = book(other)
    assert r3.status_code == 409 and "conflict" in r3.json()["detail"].lower() and "no number was issued" in r3.json()["detail"]
    db = SessionLocal()
    try:
        db.execute(__import__("sqlalchemy").text("UPDATE sequence_configs SET status = 'RETIRED' WHERE id = :i"), {"i": dup_id})
        db.commit()
    finally:
        db.close()
    assert "SEQUENCE_CONFLICT" in client.get("/api/sequences/history/export", headers=h).text


def test_job_numbers_follow_the_shipment_type_sequence(h):
    c = create(h, "job", "Sea export jobs", comps("SWAY", "~/", "shipment", "~/", "fy", "~/", "number"), number_width=6, shipment_scope="Sea Export", shipment_codes={"Sea Export": "SE"})
    activate(h, c["id"])
    j1 = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL"}, headers=h).json()["job_number"]
    assert j1.startswith("SWAY/SE/") and j1.endswith("/000001")
    air = client.post("/api/shipments", json={"mode": "Air", "direction": "Export"}, headers=h).json()["job_number"]
    assert air.startswith("AIREXP-")  # other shipment types keep the default numbering
    imp = client.post("/api/shipments", json={"mode": "Sea", "direction": "Import"}, headers=h).json()["job_number"]
    assert imp.startswith("SEAIMP-")


def test_reset_policies_counter_exhaustion_and_retries(h, world):
    db = SessionLocal()
    try:
        from sqlalchemy import select
        from app.models import Organization, SequenceConfig, User
        user = db.scalar(select(User).where(User.username == world["erp"]))
        org = user.organization_id
        db.execute(__import__("sqlalchemy").text("UPDATE sequence_configs SET status = 'INACTIVE' WHERE organization_id = :o AND doc_type = 'opportunity'"), {"o": org})
        fy = SequenceConfig(organization_id=org, name="FY reset", module="Sales & CRM", doc_type="opportunity", components=comps("OP", "~/", "fy", "~/", "number"), number_width=3, start_number=1, step=1, reset_policy="FY", max_length=40, fy_format="YY-YY", shipment_codes={}, status="ACTIVE", version=1)
        db.add(fy)
        db.commit()
        a = sq.issue(db, org, "opportunity", on=date(2026, 5, 1)); b = sq.issue(db, org, "opportunity", on=date(2026, 6, 1)); c = sq.issue(db, org, "opportunity", on=date(2027, 4, 2))
        db.commit()
        assert (a, b, c) == ("OP/26-27/001", "OP/26-27/002", "OP/27-28/001")  # restarts with the financial year, never repeats a full identifier
        fy.reset_policy = "CALENDAR"
        db.commit()
        d = sq.issue(db, org, "opportunity", on=date(2026, 5, 1))
        db.commit()
        assert d == "OP/26-27/003"  # the new calendar counter restarts at 1, but identifiers that already exist are skipped, never reused
    finally:
        db.close()


def test_counter_exhaustion_is_refused_not_truncated(h):
    c = create(h, "followup", "Tiny", comps("F", "~-", "number"), number_width=2, start_number=98)
    activate(h, c["id"])
    got = [client.post("/api/sequences/issue", json={"doc_type": "followup"}, headers=h) for _ in range(3)]
    assert [g.status_code for g in got] == [200, 200, 409]
    assert [g.json().get("number") for g in got[:2]] == ["F-98", "F-99"] and "exhausted" in got[2].json()["detail"]


def test_concurrent_issuing_gives_unique_numbers(h):
    c = create(h, "enquiry", "Concurrent", comps("EQ", "~-", "number"), number_width=6)
    deactivate_all(h, "enquiry")
    activate(h, c["id"])
    out = []

    def go():
        r = TestClient(app).post("/api/sequences/issue", json={"doc_type": "enquiry"}, headers=h)
        out.append((r.status_code, r.json().get("number")))

    threads = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    nums = [n for s, n in out if s == 200]
    assert len(nums) == 20 and len(set(nums)) == 20 and sorted(nums) == [f"EQ-{i:06d}" for i in range(1, 21)]


def test_adjust_next_number_only_forward_with_reason_and_audit(h):
    c = create(h, "lead", "Lead seq", comps("LD", "~-", "number"), number_width=5, start_number=1)
    deactivate_all(h, "lead")
    activate(h, c["id"])
    client.post("/api/sequences/issue", json={"doc_type": "lead"}, headers=h)
    assert client.post(f"/api/sequences/{c['id']}/adjust-next", json={"next_number": 1, "reason": "go back please"}, headers=h).status_code == 409
    assert client.post(f"/api/sequences/{c['id']}/adjust-next", json={"next_number": 50, "reason": "x"}, headers=h).status_code == 422
    ok = client.post(f"/api/sequences/{c['id']}/adjust-next", json={"next_number": 50, "reason": "Align with the paper register"}, headers=h)
    assert ok.status_code == 200 and ok.json()["next_number"] == "LD-00050" and ok.json()["adjusted_from"] == 2
    assert client.post("/api/sequences/issue", json={"doc_type": "lead"}, headers=h).json()["number"] == "LD-00050"
    ev = client.get(f"/api/sequences/{c['id']}/history", headers=h).json()["events"]
    adj = next(e for e in ev if e["action"] == "NEXT_NUMBER_ADJUSTED")
    assert adj["reason"] == "Align with the paper register" and adj["previous"]["next_number"] == 2 and adj["new"]["next_number"] == 50 and adj["user"]


def test_suggestions_are_valid_explained_and_never_activated(h):
    s = client.post("/api/sequences/suggest", json={"doc_type": "invoice"}, headers=h).json()
    assert s["suggestions"] and all(x["reason"] and x["samples"] for x in s["suggestions"])
    assert all(x["worst_length"] <= 16 for x in s["suggestions"] if x["valid"]) and any(x["valid"] for x in s["suggestions"])
    before = len(seqs(h))
    client.post("/api/sequences/suggest", json={"doc_type": "job"}, headers=h)
    assert len(seqs(h)) == before  # a suggestion is only a proposal


def test_permissions_tenant_isolation_and_export(h, lic, world):
    viewer = mkuser(h, lic, "VIEWER")
    ops = mkuser(h, lic, "OPERATIONS")
    assert client.get("/api/sequences", headers=viewer).status_code == 200
    assert client.post("/api/sequences", json={"name": "No way", "doc_type": "quotation", "components": comps("QX", "~-", "number")}, headers=viewer).status_code == 403
    assert client.post("/api/sequences", json={"name": "Ops try", "doc_type": "quotation", "components": comps("QX", "~-", "number")}, headers=ops).status_code == 403
    c = create(h, "quotation", "Admin draft", comps("QZ", "~-", "number"))
    assert client.post(f"/api/sequences/{c['id']}/activate", json={}, headers=viewer).status_code == 403
    assert client.patch(f"/api/sequences/{c['id']}", json={"name": "hacked"}, headers=viewer).status_code == 403
    assert client.get("/api/sequences/export", headers=viewer).status_code == 403
    assert client.get("/api/sequences/export", headers=h).status_code == 200 and "Quotation" in client.get("/api/sequences/export", headers=h).text
    assert client.get("/api/sequences/history/export", headers=h).status_code == 200
    # a user who may configure sequences but not financial ones
    from app.roles import ensure_role
    db = SessionLocal()
    try:
        from sqlalchemy import select
        from app.models import Permission, Role, User
        role_user = db.scalar(select(User).where(User.username == world["erp"]))
        # the tenant tester role of the world fixture has only customer permissions: add sequence rights to a fresh custom role
        name = "cfg" + uuid.uuid4().hex[:6]
        role = Role(name="SEQ-" + name, organization_id=role_user.organization_id, permissions=list(db.scalars(select(Permission).where(Permission.code.in_(["sequence.view", "sequence.create", "sequence.edit"])))))
        u = User(email=name + "@t.test", username=name, password_hash=__import__("app.security", fromlist=["hash_password"]).hash_password(PASSWORD), first_name="C", organization_id=role_user.organization_id, status="ACTIVE")
        u.roles.append(role)
        db.add_all([role, u])
        db.commit()
    finally:
        db.close()
    cfg = hdr(name, lic)
    assert client.post("/api/sequences", json={"name": "Non fin", "doc_type": "quotation", "components": comps("QY", "~-", "number")}, headers=cfg).status_code == 201
    assert client.post("/api/sequences", json={"name": "Fin", "doc_type": "invoice", "components": comps("IV", "~-", "number")}, headers=cfg).status_code == 403
    # another company sees nothing of this one's sequences
    other = hdr(world["demo"], portal="demo")
    assert client.get(f"/api/sequences/{c['id']}", headers=other).status_code in (403, 404)
