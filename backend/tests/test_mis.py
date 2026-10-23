import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"
D = Decimal


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def post_doc(h, doc_type, party, rate, job=None, ref=None):
    body = {"doc_type": doc_type, "party_name": party, "lines": [{"charge_name": "Ocean Freight", "qty": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0"}]}
    if job:
        body["job_number"] = job
    if ref:
        body["ref_document_id"] = ref
    d = client.post("/api/documents", json=body, headers=h)
    assert d.status_code == 201, d.text
    p = client.post(f"/api/documents/{d.json()['id']}/post", headers=h)
    assert p.status_code == 200, p.text
    return p.json()


def test_mis_reports_agree_with_the_documents(h):
    uid = uuid.uuid4().hex[:5]
    party = "Mis Cust " + uid
    cust = client.post("/api/customers", json={"code": "MS" + uid.upper(), "name": party, "roles": ["shipper"], "data": {}}, headers=h).json()
    vend = "Mis Vend " + uid
    client.post("/api/customers", json={"code": "MV" + uid.upper(), "name": vend, "roles": ["carrier"], "data": {}}, headers=h)
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN", "customer_id": cust["id"]}, headers=h).json()["job_number"]
    inv = post_doc(h, "invoice", party, "10000", job)
    post_doc(h, "purchase_invoice", vend, "6000", job)
    post_doc(h, "credit_note", party, "1000", job, ref=inv["id"])
    gp = client.get("/api/mis/gp-analysis", headers=h).json()
    row = next(j for j in gp["jobs"] if j["job"] == job)
    assert row["revenue"] == "9000.00" and row["cost"] == "6000.00" and row["gross_profit"] == "3000.00" and row["margin_percent"] == "33.33" and row["mode"] == "Sea Export"
    cp = client.get("/api/mis/customer-profitability", headers=h).json()
    c = next(x for x in cp["customers"] if x["customer"] == party)
    assert c["revenue"] == "9000.00" and c["cost"] == "6000.00" and c["jobs"] == 1
    rev = client.get("/api/mis/revenue", headers=h).json()
    assert D(next(x for x in rev["customers"] if x["customer"] == party)["revenue"]) == D("9000.00") and D(rev["total"]) == sum(D(m["revenue"]) for m in rev["months"])
    ex = client.get("/api/mis/executive", headers=h).json()
    assert D(ex["gross_profit"]) == D(ex["revenue"]) - D(ex["cost"]) and len(ex["trend"]) == 12 and ex["jobs_total"] >= 1 and D(ex["receivable"]) >= D("9000.00") - D("1000.00")
    assert client.get("/api/mis/executive?date_from=2099-01-01", headers=h).json()["revenue"] == "0.00"
    sh = client.get("/api/mis/shipments", headers=h).json()
    assert sh["total"] >= 1 and any(m["name"] == "Sea Export" for m in sh["modes"]) and any("INMAA" in l["name"] for l in sh["lanes"])
    pf = client.get("/api/mis/portfolio", headers=h).json()
    assert pf["parties"] >= 2 and any(t["customer"] == party for t in pf["top_customers"])
    ct = client.get("/api/mis/carrier-teu", headers=h).json()
    assert ct["total_teu"] >= 0 and isinstance(ct["carriers"], list)


def test_carrier_wise_teu_counts_20_as_one_and_40_as_two(h):
    from datetime import date

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Booking, JobDetail, Shipment

    uid = uuid.uuid4().hex[:5]
    carrier = "Mis Line " + uid
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN"}, headers=h).json()["job_number"]
    with SessionLocal() as db:
        s = db.scalars(select(Shipment).where(Shipment.job_number == job).order_by(Shipment.id.desc())).first()
        b = Booking(organization_id=s.organization_id, booking_date=date.today(), carrier=carrier, party_name="x", status="CONFIRMED")
        db.add(b)
        db.flush()
        s.booking_id = b.id
        db.add(JobDetail(organization_id=s.organization_id, shipment_id=s.id, containers=[{"container_no": "A1", "type": "20GP"}, {"container_no": "A2", "type": "40HC"}, {"container_no": "A3", "type": "40GP"}]))
        db.commit()
    ct = client.get("/api/mis/carrier-teu", headers=h).json()
    row = next(c for c in ct["carriers"] if c["carrier"] == carrier)
    assert row == {"carrier": carrier, "jobs": 1, "c20": 1, "c40": 2, "teu": 5}


def test_branch_and_salesperson_mis_group_revenue_and_cost_by_the_job(h):
    uid = uuid.uuid4().hex[:4]
    branch, sp = "BR-" + uid, "Sales " + uid
    j1 = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "SGSIN", "branch": branch, "salesperson": sp}, headers=h)
    j2 = client.post("/api/shipments", json={"mode": "Air", "direction": "Export", "pol": "INMAA", "pod": "SGSIN"}, headers=h)
    assert j1.status_code == 201 and j2.status_code == 201
    n1, n2 = j1.json()["job_number"], j2.json()["job_number"]
    post_doc(h, "invoice", "Mis Branch Co " + uid, "1000", job=n1)
    post_doc(h, "purchase_invoice", "Mis Branch Vendor " + uid, "400", job=n1)
    post_doc(h, "invoice", "Mis Branch Co " + uid, "300", job=n2)
    b = {r["name"]: r for r in client.get("/api/mis/branch", headers=h).json()["rows"]}
    assert D(b[branch]["revenue"]) == D("1000") and D(b[branch]["cost"]) == D("400") and D(b[branch]["gross_profit"]) == D("600") and b[branch]["jobs"] == 1 and b[branch]["gp_percent"] == "60.0"
    assert D(b["Unassigned"]["revenue"]) >= D("300")                       # the job without a branch is shown, not lost
    s = {r["name"]: r for r in client.get("/api/mis/salesperson", headers=h).json()["rows"]}
    assert D(s[sp]["revenue"]) == D("1000") and D(s[sp]["gross_profit"]) == D("600")
    # a job made from a booking carries the booking's branch and the sales person of its quotation / booking
    from datetime import date, timedelta
    body = {"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "booking_type": "Direct", "party_name": "Mis Co", "carrier": "MSC", "carrier_booking_no": "MB1", "vessel": "V", "pol": "INMAA", "pod": "BEANR",
            "etd": str(date.today() + timedelta(days=10)), "eta": str(date.today() + timedelta(days=30)), "si_cutoff": str(date.today() + timedelta(days=1)), "vgm_cutoff": str(date.today() + timedelta(days=1)),
            "data": {"services": "Freight", "branch": "Chennai HO", "sales_person": "Booked Seller", "charges": []}}
    bk = client.post("/api/bookings", json=body, headers=h).json()
    jn = client.post(f"/api/bookings/{bk['id']}/confirm", headers=h).json()["job_number"]
    job = next(x for x in client.get("/api/shipments", headers=h).json() if x["job_number"] == jn)
    assert job["branch"] == "Chennai HO" and job["salesperson"] == "Booked Seller"
