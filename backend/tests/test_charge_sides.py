import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def mk_charge(h, code, name, ar_ap):
    r = client.post("/api/masters/charge", json={"code": code, "name": name, "data": {"ar_ap": ar_ap, "hsn_code": "996511", "gst_percent": "0"}}, headers=h)
    assert r.status_code == 201, r.text


def test_ap_only_and_ar_only_charges_cannot_carry_the_other_side_on_a_job_or_a_quotation(h):
    u = uuid.uuid4().hex[:5]
    ap, ar, both = "Side AP " + u, "Side AR " + u, "Side Both " + u
    mk_charge(h, "SAP" + u, ap, "AP")
    mk_charge(h, "SAR" + u, ar, "AR")
    mk_charge(h, "SBO" + u, both, "Both")
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN"}, headers=h).json()["job_number"]
    side = lambda rate: {"ccy": "INR", "exch": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0"}
    put = lambda lines: client.put(f"/api/shipments/by-number/{job}/charges", json={"lines": lines}, headers=h)
    line = lambda name, buy, sell: {"charge_name": name, "qty": "1", "hsn": "996511", "buy": side(buy), "sell": side(sell)}
    bad = put([line(ap, "100", "150")])
    assert bad.status_code == 422 and "purchase (AP) charge" in bad.json()["detail"] and "cannot carry a sell rate" in bad.json()["detail"]
    bad2 = put([line(ar, "100", "150")])
    assert bad2.status_code == 422 and "sales (AR) charge" in bad2.json()["detail"]
    assert put([line(ap, "100", "0"), line(ar, "0", "150"), line(both, "100", "150")]).status_code == 200      # each only on its own side
    # quotations
    q = lambda *lines: client.post("/api/quotations", json={"party_name": "Acme Exports", "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "DEHAM", "lines": list(lines)}, headers=h)
    ql = lambda name, side_, rate: {"side": side_, "charge_name": name, "basis": "Per Shipment", "ccy": "INR", "exch": "1", "rate": rate, "qty": "1", "tax_pct": "0"}
    assert q(ql(ap, "SELL", "100")).status_code == 422
    assert q(ql(ar, "BUY", "100")).status_code == 422
    ok = q(ql(ap, "BUY", "100"), ql(ar, "SELL", "150"), ql(both, "SELL", "10"), ql(both, "BUY", "5"))
    assert ok.status_code == 201, ok.text
    assert q(ql("Not in master " + u, "SELL", "5")).status_code == 201                  # a charge that is not in the master is not restricted


def test_party_defaults_job_lines_and_purchase_bill_party(h):
    u = uuid.uuid4().hex[:5]
    client.put("/api/company/doc-profile", json={"state": "Maharashtra", "state_code": "27", "gstin": "27AAACS1234A1Z5"}, headers=h)
    vend = "Pd Vendor " + u
    other = "Pd Local " + u
    v = client.post("/api/customers", json={"code": "PV" + u.upper(), "name": vend, "roles": ["carrier"], "gstin": "33AACCM4945P1Z2", "data": {"state": "Tamil Nadu", "state_code": "33"}}, headers=h)
    assert v.status_code == 201, v.text
    loc = client.post("/api/customers", json={"code": "PL" + u.upper(), "name": other, "roles": ["carrier"], "gstin": "27AABCU9603R1ZM", "data": {}}, headers=h)
    assert loc.status_code == 201, loc.text
    pd = client.get(f"/api/documents/billing/party-defaults?party={vend}&doc_type=purchase_invoice", headers=h).json()
    assert pd["party_gstin"] == "33AACCM4945P1Z2" and pd["state_code"] == "33" and pd["place_of_supply"] == "27-Maharashtra" and pd["supply_type"] == "inter" and pd["customer_id"] == v.json()["id"]
    assert client.get(f"/api/documents/billing/party-defaults?party={other}&doc_type=purchase_invoice", headers=h).json()["supply_type"] == "intra"      # Maharashtra to Maharashtra
    sale = client.get(f"/api/documents/billing/party-defaults?party={vend}&doc_type=invoice", headers=h).json()
    assert sale["place_of_supply"] == "33-Tamil Nadu" and sale["supply_type"] == "inter"                                                     # sold from Maharashtra to Tamil Nadu
    assert client.get("/api/documents/billing/party-defaults?party=Nobody&doc_type=invoice", headers=h).json()["known"] is False
    # a job whose customer is somebody else: the purchase bill is the vendor's, not the job customer's
    shipper = client.post("/api/customers", json={"code": "PS" + u.upper(), "name": "Pd Shipper " + u, "roles": ["shipper"], "data": {}}, headers=h).json()
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN", "customer_id": shipper["id"]}, headers=h).json()["job_number"]
    side = lambda rate, party=None, key=None: {"ccy": "INR", "exch": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0", **({"party": party, "use_party": True} if party else {})}
    lines = [{"charge_name": "Ocean Freight", "qty": "1", "hsn": "996511", "buy": side("700", vend), "sell": side("900", "Pd Shipper " + u, "client")},
             {"charge_name": "THC", "qty": "2", "hsn": "996511", "buy": side("100", other), "sell": side("0")}]
    assert client.put(f"/api/shipments/by-number/{job}/charges", json={"lines": lines}, headers=h).status_code == 200
    bj = client.get(f"/api/documents/billing/billable-jobs?party={vend}&doc_type=purchase_invoice", headers=h).json()
    assert [x["job_number"] for x in bj] == [job] and bj[0]["lines"] == 1 and bj[0]["amount"] == "700.00"
    assert [x["job_number"] for x in client.get(f"/api/documents/billing/billable-jobs?party=Pd Shipper {u}&doc_type=invoice", headers=h).json()] == [job]
    assert client.get(f"/api/documents/billing/billable-jobs?party={vend}&doc_type=invoice", headers=h).json() == []          # the vendor is not billed
    jl = client.get(f"/api/documents/billing/job-lines?job_number={job}&party={vend}&doc_type=purchase_invoice", headers=h).json()
    assert len(jl) == 1 and jl[0]["charge_name"] == "Ocean Freight" and jl[0]["rate"] == "700.0000" and jl[0]["job_number"] == job
    made = client.post("/api/documents/from-job", json={"job_number": job, "doc_type": "purchase_invoice", "party": vend}, headers=h)
    assert made.status_code == 201, made.text
    d = made.json()
    assert d["customer_id"] == v.json()["id"] and d["party_gstin"] == "33AACCM4945P1Z2" and d["place_of_supply"] == "27-Maharashtra" and d["supply_type"] == "inter"
    assert client.get(f"/api/documents/billing/billable-jobs?party={vend}&doc_type=purchase_invoice", headers=h).json() == []     # now billed
    posted = client.post(f"/api/documents/{d['id']}/post", headers=h)
    assert posted.status_code == 200
    gl = client.get("/api/coa/ledgers?source_type=customer", headers=h).json()
    mine = [l for l in gl if l["name"] == vend and l["source_role"] == "creditor"]
    assert len(mine) == 1 and not [l for l in gl if l["name"] == "Pd Shipper " + u and l["source_role"] == "creditor"]


def test_charge_parties_clients_are_the_job_parties_and_vendors_the_vendor_category(h):
    u = uuid.uuid4().hex[:5]
    shipper = client.post("/api/customers", json={"code": "CS" + u.upper(), "name": "Cp Shipper " + u, "roles": ["shipper"], "data": {}}, headers=h).json()
    vendor = client.post("/api/customers", json={"code": "CV" + u.upper(), "name": "Cp Carrier " + u, "roles": ["carrier"], "data": {}}, headers=h).json()
    stranger = client.post("/api/customers", json={"code": "CX" + u.upper(), "name": "Cp Stranger " + u, "roles": ["shipper"], "data": {}}, headers=h).json()
    job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN", "customer_id": shipper["id"]}, headers=h).json()["job_number"]
    r = client.get(f"/api/shipments/by-number/{job}/charge-parties", headers=h)
    assert r.status_code == 200, r.text
    cp = r.json()
    assert cp["clients"] == ["Cp Shipper " + u]                        # only the parties of this job
    assert "Cp Carrier " + u in cp["vendors"] and "Cp Shipper " + u not in cp["vendors"] and "Cp Stranger " + u not in cp["vendors"]
    sv = client.put(f"/api/jobs/{job}/details/parties", json={"data": {"shipper": "Cp Shipper " + u, "consignee": "Cp Stranger " + u, "notify1": "Same as consignee"}}, headers=h)
    assert sv.status_code == 200, sv.text
    cp2 = client.get(f"/api/shipments/by-number/{job}/charge-parties", headers=h).json()
    assert cp2["clients"] == ["Cp Shipper " + u, "Cp Stranger " + u]      # the parties saved on the job, each once
