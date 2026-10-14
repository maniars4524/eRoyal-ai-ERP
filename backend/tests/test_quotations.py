import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import config
from app.billing import fiscal_year
from app.db import SessionLocal
from app.main import app
from app.models import Quotation

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


def line(name="Ocean Freight", carrier="", rate="850", ccy="USD", exch="83.5", qty="2", basis="Per Container", tax="0", side="SELL", **extra):
    return {"side": side, "charge_name": name, "carrier": carrier, "basis": basis, "ccy": ccy, "exch": exch, "rate": rate, "qty": qty, "tax_pct": tax, **extra}


def draft(h, **extra):
    body = {"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "DEHAM",
            "lines": [line(), line("THC", rate="6500", ccy="INR", exch="1", qty="2", basis="Per Container")], **extra}
    r = client.post("/api/quotations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def approved(h, h2, **extra):
    q = draft(h, **extra)
    assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).status_code == 200
    r = client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2)
    assert r.status_code == 200, r.text
    return r.json()


def issued(h, h2, **extra):
    q = approved(h, h2, **extra)
    r = client.post(f"/api/quotations/{q['id']}/send", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ calculations
def test_line_amounts_are_calculated_on_the_server_with_both_currencies_stored(h):
    q = draft(h, lines=[line(tax="18", taxable=True), line("THC", rate="6500", ccy="INR", exch="1", qty="2", tax="18", taxable=False)])
    a, b = q["lines"]
    # USD 850 x 2 units = 1,700.00 USD -> x 83.5 = INR 141,950.00; GST 18% = 25,551.00; total 167,501.00
    assert a["amount_fcy"] == "1700.00" and a["amount"] == "141950.00" and a["gst_amount"] == "25551.00" and a["total_inr"] == "167501.00" and a["taxable"] is True
    # non-taxable: GST is zero even though a % was supplied
    assert b["amount_fcy"] == "13000.00" and b["amount"] == "13000.00" and b["gst_amount"] == "0.00" and b["tax_pct"] == "0.00" and b["taxable"] is False
    assert q["quoted_amount"] == "154950.00" and q["lines"][0]["side"] == "SELL"


def test_legacy_payload_without_side_or_taxable_still_works(h):
    q = draft(h, lines=[{"charge_name": "Old style", "carrier": "", "basis": "Flat", "ccy": "INR", "exch": "1", "rate": "1000", "qty": "1", "tax_pct": "18"}])
    l = q["lines"][0]
    assert l["side"] == "SELL" and l["taxable"] is True and l["gst_amount"] == "180.00" and l["total_inr"] == "1180.00"


def test_container_wise_rates_each_type_has_its_own_rate(h):
    breakup = [{"type": "20GP", "qty": "2", "rate": "850"}, {"type": "40GP", "qty": "1", "rate": "1450"}, {"type": "40HC", "qty": "2", "rate": "1600"}]
    q = draft(h, lines=[line("Ocean Freight", basis="Container", exch="80", container_breakup=breakup)])
    l = q["lines"][0]
    assert l["qty"] == "5.000" and l["amount_fcy"] == "6350.00" and l["amount"] == "508000.00" and l["rate"] == "1270.0000"
    assert [(b["type"], b["qty"], b["rate"]) for b in l["container_breakup"]] == [("20GP", "2", "850"), ("40GP", "1", "1450"), ("40HC", "2", "1600")]
    # editing the breakup recalculates everything
    upd = client.patch(f"/api/quotations/{q['id']}", json={"lines": [line("Ocean Freight", basis="Container", exch="80", container_breakup=[{"type": "40HC", "qty": "3", "rate": "1600"}])]}, headers=h).json()
    assert upd["lines"][0]["amount_fcy"] == "4800.00" and upd["lines"][0]["qty"] == "3.000"
    # separate rows per container type are equally valid
    sep = draft(h, lines=[line("Ocean Freight", basis="Container", container_type="20GP", qty="2", rate="850", ccy="INR", exch="1"), line("Ocean Freight", basis="Container", container_type="40HC", qty="2", rate="1600", ccy="INR", exch="1")])
    assert [l["container_type"] for l in sep["lines"]] == ["20GP", "40HC"] and sep["quoted_amount"] == "4900.00"


def test_cost_sheet_matches_the_specified_example_and_margin_is_not_markup(h):
    sell = {"Ocean Freight": 150000, "THC": 25000, "Documentation": 8000, "Transportation": 35000, "Customs Clearance": 12000}
    buy = {"Ocean Freight": 120000, "THC": 18000, "Documentation": 4000, "Transportation": 29000, "Customs Clearance": 8000}
    lines = [line(n, rate=str(v), ccy="INR", exch="1", qty="1", basis="Flat", tax="18", taxable=True) for n, v in sell.items()] + [line(n, rate=str(v), ccy="INR", exch="1", qty="1", basis="Flat", side="BUY", tax="18", taxable=True) for n, v in buy.items()]
    q = draft(h, lines=lines)
    cs = q["cost_sheet"]
    assert cs["total_sell"] == "230000.00" and cs["total_buy"] == "179000.00" and cs["gross_margin"] == "51000.00"
    assert cs["margin_pct"] == "22.17" and cs["markup_pct"] == "28.49"  # (230-179)/230 vs (230-179)/179
    rows = {r["charge"]: r for r in cs["rows"]}
    assert rows["Ocean Freight"]["margin"] == "30000.00" and rows["THC"]["margin"] == "7000.00" and rows["Documentation"]["margin"] == "4000.00" and rows["Transportation"]["margin"] == "6000.00" and rows["Customs Clearance"]["margin"] == "4000.00"
    assert cs["sell_gst"] == "41400.00" and cs["buy_gst"] == "32220.00" and cs["grand_total_sell"] == "271400.00" and cs["result"] == "PROFIT"
    # margin is commercial: GST does not enter it
    ocean = next(l for l in q["lines"] if l["charge_name"] == "Ocean Freight" and l["side"] == "SELL")
    assert ocean["margin_rate"] == "20.00"  # charge-level margin (150000-120000)/150000


def test_loss_break_even_and_missing_counterpart(h):
    loss = draft(h, lines=[line("Freight", rate="100", ccy="INR", exch="1", qty="1"), line("Freight", rate="130", ccy="INR", exch="1", qty="1", side="BUY")])["cost_sheet"]
    assert loss["gross_margin"] == "-30.00" and loss["result"] == "LOSS" and loss["margin_pct"] == "-30.00"
    even = draft(h, lines=[line("Freight", rate="100", ccy="INR", exch="1", qty="1"), line("Freight", rate="100", ccy="INR", exch="1", qty="1", side="BUY")])["cost_sheet"]
    assert even["result"] == "BREAK_EVEN"
    sell_only = draft(h, lines=[line("Freight", rate="100", ccy="INR", exch="1", qty="1")])
    assert sell_only["cost_sheet"]["markup_pct"] is None and sell_only["lines"][0]["margin_rate"] is None


def test_independent_buy_and_sell_currencies_rates_and_units(h):
    q = draft(h, lines=[line("Freight", rate="850", ccy="USD", exch="83.5", qty="2"), line("Freight", rate="700", ccy="EUR", exch="90", qty="3", side="BUY", remarks="carrier contract X")])
    sell, buy = q["lines"]
    assert sell["amount"] == "141950.00" and buy["amount"] == "189000.00" and buy["remarks"] == "carrier contract X" and buy["side"] == "BUY"
    assert q["cost_sheet"]["gross_margin"] == "-47050.00"


# ------------------------------------------------------------------ buy costs are internal
def test_buy_costs_and_margins_are_hidden_from_users_without_cost_permission(world, admin, h):
    q = draft(h, lines=[line("Freight", rate="100", ccy="INR", exch="1", qty="1"), line("Freight", rate="60", ccy="INR", exch="1", qty="1", side="BUY")])
    tag = uuid.uuid4().hex[:6]
    client.post("/api/users", json={"username": f"vw{tag}", "email": f"vw{tag}@c.test", "password": PASSWORD, "first_name": "V", "role": "VIEWER"}, headers=h)
    viewer = hdr(f"vw{tag}", admin["license"])
    seen = client.get(f"/api/quotations/{q['id']}", headers=viewer).json()
    assert [l["side"] for l in seen["lines"]] == ["SELL"] and "cost_sheet" not in seen and seen["can_view_cost"] is False
    assert "BUY" not in str(seen["lines"])
    mine = client.get(f"/api/quotations/{q['id']}", headers=h).json()
    assert [l["side"] for l in mine["lines"]] == ["SELL", "BUY"] and mine["cost_sheet"]["gross_margin"] == "40.00" and mine["can_view_cost"] is True


# ------------------------------------------------------------------ approval workflow
def test_submit_validation_and_lock_after_submission(h):
    empty = client.post("/api/quotations", json={"party_name": "X"}, headers=h).json()
    assert client.post(f"/api/quotations/{empty['id']}/approval/submit", headers=h).status_code == 422
    buy_only = client.post("/api/quotations", json={"party_name": "X", "lines": [line(side="BUY")]}, headers=h).json()
    assert client.post(f"/api/quotations/{buy_only['id']}/approval/submit", headers=h).status_code == 422
    noparty = client.post("/api/quotations", json={"party_name": "", "lines": [line()]}, headers=h).json()
    assert client.post(f"/api/quotations/{noparty['id']}/approval/submit", headers=h).status_code == 422
    q = draft(h)
    done = client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).json()
    assert done["approval_status"] == "SUBMITTED" and done["editable"] is False and done["prepared_by_name"]
    assert client.patch(f"/api/quotations/{q['id']}", json={"party_name": "x"}, headers=h).status_code == 409
    assert client.delete(f"/api/quotations/{q['id']}", headers=h).status_code == 409
    assert client.post(f"/api/quotations/{q['id']}/send", headers=h).status_code == 409  # not approved yet
    assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).status_code == 409


def test_segregation_of_duties_and_permission_to_approve(world, admin, h, h2, monkeypatch):
    q = draft(h)
    client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h)
    own = client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h)
    assert own.status_code == 403 and "Segregation" in own.json()["detail"]
    tag = uuid.uuid4().hex[:6]
    client.post("/api/users", json={"username": f"op{tag}", "email": f"op{tag}@c.test", "password": PASSWORD, "first_name": "O", "role": "OPERATIONS"}, headers=h)
    ops = hdr(f"op{tag}", admin["license"])
    assert client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=ops).status_code == 403  # no quote.approve
    ok = client.post(f"/api/quotations/{q['id']}/approval/approve", json={"remarks": "Rates verified"}, headers=h2).json()
    assert ok["approval_status"] == "APPROVED" and ok["approved_by_name"] and ok["approved_at"] and ok["approval_remarks"] == "Rates verified"
    assert client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2).status_code == 409
    # with the rule switched off the preparer may approve their own quotation
    monkeypatch.setattr(config, "QUOTE_SOD", False)
    q2 = draft(h)
    client.post(f"/api/quotations/{q2['id']}/approval/submit", headers=h)
    assert client.post(f"/api/quotations/{q2['id']}/approval/approve", json={}, headers=h).status_code == 200


def test_rejection_requires_a_reason_returns_for_correction_and_can_be_resubmitted(h, h2):
    q = draft(h)
    client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h)
    assert client.post(f"/api/quotations/{q['id']}/approval/reject", json={}, headers=h2).status_code == 422
    rej = client.post(f"/api/quotations/{q['id']}/approval/reject", json={"remarks": "Margin too low"}, headers=h2).json()
    assert rej["approval_status"] == "REJECTED" and rej["approval_remarks"] == "Margin too low" and rej["editable"] is True and rej["approved_by"] is None
    fix = client.patch(f"/api/quotations/{q['id']}", json={"lines": [line("Ocean Freight", rate="900")]}, headers=h)
    assert fix.status_code == 200
    again = client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).json()
    assert again["approval_status"] == "SUBMITTED"
    assert client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2).json()["approval_status"] == "APPROVED"
    trail = client.get(f"/api/quotations/{q['id']}/events", headers=h).json()
    assert [e["action"] for e in trail] == ["CREATED", "SUBMITTED", "REJECTED", "SUBMITTED", "APPROVED"] and trail[2]["remarks"] == "Margin too low" and all(e["user"] for e in trail)


def test_approved_quotation_cannot_be_modified_but_can_be_revised(h, h2):
    q = approved(h, h2)
    assert client.patch(f"/api/quotations/{q['id']}", json={"party_name": "Sneaky"}, headers=h).status_code == 409
    rev = client.post(f"/api/quotations/{q['id']}/revise", headers=h)
    assert rev.status_code == 201, rev.text
    r1 = rev.json()
    assert r1["revision_no"] == 1 and r1["status"] == "DRAFT" and r1["approval_status"] == "DRAFT" and r1["number"] is None and r1["root_id"] == q["root_id"] and len(r1["lines"]) == len(q["lines"])
    assert client.get(f"/api/quotations/{q['id']}", headers=h).json()["status"] == "SUPERSEDED"
    assert client.post(f"/api/quotations/{q['id']}/revise", headers=h).status_code == 409  # a newer revision exists
    chain = client.get(f"/api/quotations/{r1['id']}/revisions", headers=h).json()
    assert [c["revision_no"] for c in chain] == [0, 1] and chain[1]["current"] is True


def test_issued_quotation_revision_keeps_the_number_with_a_suffix(h, h2):
    first = issued(h, h2)
    assert client.get(f"/api/quotations/{first['id']}", headers=h).json()["status"] == "SENT"
    r1 = client.post(f"/api/quotations/{first['id']}/revise", headers=h).json()
    client.post(f"/api/quotations/{r1['id']}/approval/submit", headers=h)
    client.post(f"/api/quotations/{r1['id']}/approval/approve", json={}, headers=h2)
    sent = client.post(f"/api/quotations/{r1['id']}/send", headers=h).json()
    assert sent["number"] == f"{first['number']}-R1" and sent["status"] == "SENT"
    assert client.get(f"/api/quotations/{first['id']}", headers=h).json()["status"] == "SUPERSEDED"
    assert [e["action"] for e in client.get(f"/api/quotations/{r1['id']}/events", headers=h).json()][-1] == "SENT"


# ------------------------------------------------------------------ numbering and client response (unchanged behaviour)
def test_send_numbers_gap_free_and_locks(h, h2):
    nums = [issued(h, h2)["number"] for _ in range(3)]
    fy = fiscal_year(date.today())
    seq = [int(n.rsplit("/", 1)[1]) for n in nums]
    assert all(n.startswith(f"QT/{fy}/") for n in nums) and seq == list(range(seq[0], seq[0] + 3))


def test_concurrent_sends_never_duplicate_numbers(h, h2):
    ids = [approved(h, h2)["id"] for _ in range(6)]
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(TestClient(app).post(f"/api/quotations/{i}/send", headers=h).json()["number"])) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(out) == 6 and len(set(out)) == 6


def test_send_validation(h, h2):
    past = client.post("/api/quotations", json={"party_name": "X", "valid_until": str(date.today() - timedelta(days=1)), "lines": [line()]}, headers=h).json()
    assert client.post(f"/api/quotations/{past['id']}/approval/submit", headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "mode": "Teleport"}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "lines": [line(ccy="USD", exch="0")]}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "lines": [line(side="MIDDLE")]}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "lines": [line(container_breakup=[{"type": "20GP", "qty": "-1", "rate": "5"}])]}, headers=h).status_code == 422
    assert client.post("/api/quotations", json={"party_name": "X", "customer_id": 99999999}, headers=h).status_code == 404


def test_draft_can_be_deleted_but_not_a_revision(h, h2):
    q = draft(h)
    assert client.delete(f"/api/quotations/{q['id']}", headers=h).status_code == 204
    rev = client.post(f"/api/quotations/{approved(h, h2)['id']}/revise", headers=h).json()
    assert client.delete(f"/api/quotations/{rev['id']}", headers=h).status_code == 409


def test_accept_reject_and_expiry(h, h2):
    q = draft(h)
    assert client.post(f"/api/quotations/{q['id']}/accept", headers=h).status_code == 409
    sent = issued(h, h2)
    assert client.post(f"/api/quotations/{sent['id']}/reject", headers=h).json()["status"] == "REJECTED"
    assert client.post(f"/api/quotations/{sent['id']}/accept", headers=h).status_code == 409
    old = issued(h, h2)
    db = SessionLocal()
    row = db.get(Quotation, old["id"])
    row.valid_until = date.today() - timedelta(days=2)
    db.commit()
    db.close()
    got = client.get(f"/api/quotations/{old['id']}", headers=h).json()
    assert got["status"] == "SENT" and got["effective_status"] == "EXPIRED"
    assert client.post(f"/api/quotations/{old['id']}/accept", headers=h).status_code == 409
    assert old["id"] in [x["id"] for x in client.get("/api/quotations?status=EXPIRED", headers=h).json()]
    assert client.post(f"/api/quotations/{old['id']}/revise", headers=h).status_code == 201  # an expired quotation can be revised


def test_convert_carries_sell_and_matching_buy_costs_onto_the_job(h, h2):
    q = issued(h, h2, lines=[line("Ocean Freight", rate="850", qty="2"), line("THC", rate="6500", ccy="INR", exch="1", qty="2", basis="Per Container"),
                             line("Ocean Freight", rate="700", qty="2", side="BUY", carrier=""), line("THC", rate="5200", ccy="INR", exch="1", qty="2", basis="Per Container", side="BUY"), line("Agent fee", rate="1000", ccy="INR", exch="1", qty="1", basis="Flat", side="BUY")])
    client.post(f"/api/quotations/{q['id']}/accept", headers=h)
    assert client.post(f"/api/quotations/{q['id']}/convert", headers=h).status_code == 200
    body = client.get(f"/api/quotations/{q['id']}", headers=h).json()
    assert body["status"] == "CONVERTED" and body["job_number"]
    ch = client.get(f"/api/shipments/by-number/{body['job_number']}/charges", headers=h).json()
    assert len(ch["lines"]) == 3  # two sell charges (with buy attached) + the buy-only agent fee
    assert ch["totals"]["ar"] == "154950.00" and ch["totals"]["ap"] == "128300.00"  # 700x2xUSD83.5 + 5200x2 + 1000
    assert ch["totals"]["gross_profit"] == "26650.00"


def test_convert_multi_carrier_requires_a_choice(h, h2):
    q = issued(h, h2, carrier_mode="multi", lines=[line(carrier="MSC", rate="900"), line(carrier="ONE", rate="800")])
    client.post(f"/api/quotations/{q['id']}/accept", headers=h)
    assert client.post(f"/api/quotations/{q['id']}/convert", headers=h).status_code == 422
    ok = client.post(f"/api/quotations/{q['id']}/convert?carrier=ONE", headers=h)
    assert ok.status_code == 200
    charges = client.get(f"/api/shipments/by-number/{ok.json()['job_number']}/charges", headers=h).json()
    assert len(charges["lines"]) == 1 and charges["lines"][0]["sell"]["rate"].startswith("800")


# ------------------------------------------------------------------ exchange rates from the Currency Master
def test_exchange_rate_comes_from_the_currency_master_and_editing_needs_permission(world, admin, h):
    assert client.post("/api/masters/currency", json={"code": "USD", "name": "US Dollar", "data": {"exchange_rate": "83.5"}}, headers=h).status_code in (201, 409)
    tag = uuid.uuid4().hex[:6]
    client.post("/api/users", json={"username": f"sl{tag}", "email": f"sl{tag}@c.test", "password": PASSWORD, "first_name": "S", "role": "OPERATIONS"}, headers=h)
    ops = hdr(f"sl{tag}", admin["license"])
    same = client.post("/api/quotations", json={"party_name": "X", "lines": [line(exch="83.5")]}, headers=ops)
    assert same.status_code == 201, same.text
    changed = client.post("/api/quotations", json={"party_name": "X", "lines": [line(exch="90")]}, headers=ops)
    assert changed.status_code == 403 and "quote.rate_override" in changed.json()["detail"]
    allowed = client.post("/api/quotations", json={"party_name": "X", "lines": [line(exch="84.25")]}, headers=h)  # admin has the permission
    assert allowed.status_code == 201 and allowed.json()["lines"][0]["amount"] == "143225.00"
    # a currency without a master rate can be entered freely
    free = client.post("/api/quotations", json={"party_name": "X", "lines": [line(ccy="EUR", exch="91")]}, headers=ops)
    assert free.status_code == 201


# ------------------------------------------------------------------ tenancy and permissions
def test_tenant_isolation_and_permissions(world, admin, h):
    q = draft(h)
    demo = hdr(world["demo"], portal="demo")
    assert client.get(f"/api/quotations/{q['id']}", headers=demo).status_code in (403, 404)
    assert client.get(f"/api/quotations/{q['id']}/events", headers=demo).status_code in (403, 404)
    tag = uuid.uuid4().hex[:6]
    for role in ("VIEWER", "ACCOUNTS"):
        client.post("/api/users", json={"username": f"{role[:2].lower()}{tag}", "email": f"{role[:2].lower()}{tag}@q.test", "password": PASSWORD, "first_name": role, "role": role}, headers=h)
    viewer, acct = hdr(f"vi{tag}", admin["license"]), hdr(f"ac{tag}", admin["license"])
    assert client.get(f"/api/quotations/{q['id']}", headers=viewer).status_code == 200
    assert client.post("/api/quotations", json={"party_name": "x"}, headers=viewer).status_code == 403
    assert client.post("/api/quotations", json={"party_name": "x"}, headers=acct).status_code == 403
    assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=viewer).status_code == 403
    assert client.get("/api/quotations").status_code == 401
    assert client.get("/api/quotations", headers=hdr(world["dev"], portal="developer")).status_code == 403


# ------------------------------------------------------------------ numbering at submission; one-person companies
def test_the_quotation_number_is_generated_when_submitted_and_kept_through_rejection(h, h2):
    q = draft(h)
    assert q["number"] is None
    sub = client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).json()
    assert sub["approval_status"] == "SUBMITTED" and sub["number"] and sub["number"].startswith("QT/")
    rej = client.post(f"/api/quotations/{q['id']}/approval/reject", json={"remarks": "Please recheck the buy rate"}, headers=h2).json()
    assert rej["approval_status"] == "REJECTED" and rej["number"] == sub["number"]  # the number is kept
    again = client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).json()
    assert again["number"] == sub["number"]  # resubmission never takes a second number
    approved_q = client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h2).json()
    assert approved_q["number"] == sub["number"]


def test_a_company_with_no_other_approver_can_approve_its_own_quotation(h, admin):
    from sqlalchemy import select, update

    from app.models import User

    db = SessionLocal()
    try:
        me = db.scalar(select(User).where(User.username == admin["username"]))
        others = [(u.id, u.status) for u in db.scalars(select(User).where(User.organization_id == me.organization_id, User.id != me.id))]
        for uid, _ in others:
            db.execute(update(User).where(User.id == uid).values(status="INACTIVE"))
        db.commit()
        q = draft(h)
        assert client.post(f"/api/quotations/{q['id']}/approval/submit", headers=h).status_code == 200
        r = client.post(f"/api/quotations/{q['id']}/approval/approve", json={}, headers=h)
        assert r.status_code == 200 and r.json()["approval_status"] == "APPROVED"
    finally:
        for uid, status in others:
            db.execute(update(User).where(User.id == uid).values(status=status))
        db.commit()
        db.close()
