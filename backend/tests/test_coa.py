import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


@pytest.fixture(scope="module")
def h(world, admin):
    body = {"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]}
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def flat(nodes, out=None):
    out = {} if out is None else out
    for n in nodes:
        out[(n["path"], "g")] = n
        for l in n["ledgers"]:
            out[(l["path"] + " > " + l["name"], "l")] = l
        flat(n["groups"], out)
    return out


def test_default_tree_has_the_four_main_groups_sub_groups_and_system_ledgers(h):
    t = client.get("/api/coa/tree", headers=h)
    assert t.status_code == 200
    f = flat(t.json()["groups"])
    for p in ("ASSETS", "LIABILITIES", "INCOME", "EXPENSES", "ASSETS > CURRENT ASSETS > SUNDRY DEBTORS", "LIABILITIES > CURRENT LIABILITIES > SUNDRY CREDITORS", "ASSETS > CURRENT ASSETS > BANK ACCOUNTS",
              "INCOME > DIRECT INCOMES > FREIGHT FORWARDING", "EXPENSES > INDIRECT EXPENSES > STAFF SALARY", "LIABILITIES > CAPITAL ACCOUNT"):
        assert (p, "g") in f, p
    assert f[("ASSETS > CURRENT ASSETS > SUNDRY DEBTORS", "g")]["code"] == "ACSC"
    assert ("ASSETS > CURRENT ASSETS > CASH-IN-HAND > Cash in Hand", "l") in f
    assert ("LIABILITIES > CURRENT LIABILITIES > DUTIES & TAXES > Output CGST", "l") in f
    again = client.get("/api/coa/tree", headers=h).json()["groups"]
    assert len(flat(again)) == len(f)   # loading twice never duplicates


def test_customer_and_vendor_masters_create_their_ledgers_with_system_codes(h):
    a = client.post("/api/customers", json={"code": "COA-C1", "name": "Coa Client One", "roles": ["shipper"], "data": {}}, headers=h)
    b = client.post("/api/customers", json={"code": "COA-V1", "name": "Coa Vendor One", "roles": ["carrier"], "data": {}}, headers=h)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    f = flat(client.get("/api/coa/tree", headers=h).json()["groups"])
    d = f[("ASSETS > CURRENT ASSETS > SUNDRY DEBTORS > Coa Client One", "l")]
    c = f[("LIABILITIES > CURRENT LIABILITIES > SUNDRY CREDITORS > Coa Vendor One", "l")]
    assert d["code"].startswith("ACSC") and c["code"].startswith("LCSC") and d["source_type"] == "customer"
    assert ("LIABILITIES > CURRENT LIABILITIES > SUNDRY CREDITORS > Coa Client One", "l") not in f


def test_charge_and_bank_masters_create_ledgers(h):
    r = client.post("/api/masters/charge", json={"code": "COA-CH", "name": "Coa Test Charge", "data": {"module": "CUS"}}, headers=h)
    assert r.status_code == 201, r.text
    bk = client.post("/api/masters/bank", json={"code": "COA-BK", "name": "Coa Test Bank", "data": {"account_number": "1234"}}, headers=h)
    assert bk.status_code == 201, bk.text
    f = flat(client.get("/api/coa/tree", headers=h).json()["groups"])
    assert ("INCOME > DIRECT INCOMES > CHA > Coa Test Charge", "l") in f and ("EXPENSES > DIRECT EXPENSES > CHA > Coa Test Charge", "l") in f
    assert ("ASSETS > CURRENT ASSETS > BANK ACCOUNTS > Coa Test Bank - 1234", "l") in f


def test_new_group_and_ledger_edit_move_and_guards(h):
    root = {n["name"]: n for n in client.get("/api/coa/tree", headers=h).json()["groups"]}
    cur = next(g for g in root["ASSETS"]["groups"] if g["name"] == "CURRENT ASSETS")
    g = client.post("/api/coa/groups", json={"parent_id": cur["id"], "name": "Coa Deposits", "code": "ACDX"}, headers=h)
    assert g.status_code == 201 and g.json()["name"] == "COA DEPOSITS"
    assert client.post("/api/coa/groups", json={"parent_id": cur["id"], "name": "coa deposits"}, headers=h).status_code == 409
    l1 = client.post("/api/coa/ledgers", json={"group_id": g.json()["id"], "name": "Rent Deposit", "opening_balance": "5000", "opening_side": "Dr"}, headers=h)
    l2 = client.post("/api/coa/ledgers", json={"group_id": g.json()["id"], "name": "EB Deposit"}, headers=h)
    assert l1.status_code == 201 and l1.json()["code"] == "ACDX0001" and l2.json()["code"] == "ACDX0002"
    assert client.post("/api/coa/ledgers", json={"group_id": g.json()["id"], "name": "rent deposit"}, headers=h).status_code == 409
    assert client.post("/api/coa/ledgers", json={"group_id": root["ASSETS"]["id"], "name": "Direct"}, headers=h).status_code == 409   # not under a main group
    e = client.patch(f"/api/coa/ledgers/{l1.json()['id']}", json={"name": "Rent Deposit (Office)", "alias": "RD"}, headers=h)
    assert e.status_code == 200 and e.json()["name"] == "Rent Deposit (Office)" and e.json()["code"] == "ACDX0001"
    liab = next(x for x in root["LIABILITIES"]["groups"] if x["name"] == "CAPITAL ACCOUNT")
    assert client.post("/api/coa/move", json={"kind": "group", "id": g.json()["id"], "to_group_id": liab["id"]}, headers=h).status_code == 409   # stays within ASSETS
    fa = next(x for x in root["ASSETS"]["groups"] if x["name"] == "FIXED ASSETS")
    assert client.post("/api/coa/move", json={"kind": "ledger", "id": l2.json()["id"], "to_group_id": fa["id"]}, headers=h).status_code == 200
    assert client.patch(f"/api/coa/ledgers/{l2.json()['id']}", json={"status": "INACTIVE"}, headers=h).json()["status"] == "INACTIVE"
    cash = next(x for x in client.get("/api/coa/ledgers", headers=h).json() if x["name"] == "Cash in Hand")
    assert client.post("/api/coa/move", json={"kind": "ledger", "id": cash["id"], "to_group_id": fa["id"]}, headers=h).status_code == 409
    assert client.patch(f"/api/coa/ledgers/{cash['id']}", json={"name": "Petty"}, headers=h).status_code == 409
    assert client.patch(f"/api/coa/groups/{root['ASSETS']['id']}", json={"name": "X"}, headers=h).status_code == 409


def test_search_and_export(h):
    s = client.get("/api/coa/search?q=coa client", headers=h).json()
    assert any(x["name"] == "Coa Client One" for x in s)
    csv = client.get("/api/coa/export", headers=h)
    assert csv.status_code == 200 and "Coa Client One" in csv.text and csv.text.startswith("Type,Code,Name")


def test_proforma_number_exists_from_the_draft_and_party_address_is_printed(h):
    c = client.post("/api/customers", json={"code": "PF-ADDR", "name": "Pf Addr Party", "roles": ["shipper"], "data": {"address_line_1": "12 Test Road", "city": "CHENNAI", "country_2": "India (IN)"}}, headers=h)
    assert c.status_code == 201, c.text
    d = client.post("/api/documents", json={"doc_type": "invoice", "party_name": "Pf Addr Party", "lines": [{"charge_name": "X", "qty": "1", "rate": "100", "tax_type": "Taxable", "tax_pct": "18"}]}, headers=h)
    assert d.status_code == 201, d.text
    no = d.json()["header"]["proforma_no"]
    assert no.startswith("PF/")
    again = client.patch(f"/api/documents/{d.json()['id']}", json={"header": {"no_round_off": True}}, headers=h).json()
    assert again["header"]["proforma_no"] == no   # saving never changes the number
    t = client.get("/api/company/party-text?name=Pf Addr Party", headers=h).json()
    assert "12 Test Road" in t["text"] and "CHENNAI" in t["text"]


def test_bank_master_details_feed_the_printed_bank_block(h):
    r = client.post("/api/masters/bank", json={"code": "PRT-BK", "name": "Print Test Bank", "data": {"beneficiary": "Print Co", "account_number": "9988", "ifsc": "PRTB0001", "upi_id": "print@upi", "branch": "Mount Road", "currency": "INR", "default_bank": "yes"}}, headers=h)
    assert r.status_code == 201, r.text
    p = client.get("/api/company/doc-profile", headers=h).json()
    pp = p["print_profile"]
    assert pp["bank_name"] == "Print Test Bank" and pp["bank_account"] == "9988" and pp["bank_beneficiary"] == "Print Co" and pp["upi_id"] == "print@upi" and "Mount Road" in pp["bank_branch"]
    assert p["profile"].get("bank_name") != "Print Test Bank"       # the profile the user edits is not overwritten
    fx = client.post("/api/masters/bank", json={"code": "PRT-FX", "name": "Print Usd Bank", "data": {"account_number": "7711", "swift": "PRTBUS33", "currency": "USD"}}, headers=h)
    assert fx.status_code == 201
    pp = client.get("/api/company/doc-profile", headers=h).json()["print_profile"]
    assert pp["usd_bank_name"] == "Print Usd Bank" and pp["usd_bank_swift"] == "PRTBUS33" and pp["bank_name"] == "Print Test Bank"


def test_every_organization_role_lands_on_the_right_side_of_the_accounts(h):
    """Customer is a debtor; vendor, origin agent, co-loader, IATA and CFS are creditors; a customer that is also a vendor has both ledgers; one organization never gets a second record."""
    for code, name, roles in [("ROL-1", "Role Customer", ["customer"]), ("ROL-2", "Role Vendor", ["vendor"]), ("ROL-3", "Role Origin Agent", ["origin_agent"]), ("ROL-4", "Role Coloader", ["coloader"]),
                              ("ROL-5", "Role Both", ["customer", "vendor"])]:
        assert client.post("/api/customers", json={"code": code, "name": name, "roles": roles, "data": {}}, headers=h).status_code == 201
    r = client.post("/api/customers", json={"code": "ROL-6", "name": "Role Iata", "roles": ["iata"], "data": {"iata_agent_code": "RI-0001"}}, headers=h)
    r2 = client.post("/api/customers", json={"code": "ROL-7", "name": "Role Cfs", "roles": ["cfs"], "data": {"cfs_code": "RCFS1"}}, headers=h)
    assert r.status_code == 201 and r2.status_code == 201
    f = flat(client.get("/api/coa/tree", headers=h).json()["groups"])
    debtor = lambda n: ("ASSETS > CURRENT ASSETS > SUNDRY DEBTORS > " + n, "l") in f
    creditor = lambda n: ("LIABILITIES > CURRENT LIABILITIES > SUNDRY CREDITORS > " + n, "l") in f
    assert debtor("Role Customer") and not creditor("Role Customer")
    for n in ("Role Vendor", "Role Origin Agent", "Role Coloader", "Role Iata", "Role Cfs"):
        assert creditor(n) and not debtor(n), n
    assert debtor("Role Both") and creditor("Role Both")
    # the vendor pick-list (bills, bookings) includes every vendor role, the client pick-list every client role
    vend = {c["name"] for c in client.get("/api/customers?role=vendor", headers=h).json()}
    cli = {c["name"] for c in client.get("/api/customers?role=client", headers=h).json()}
    assert {"Role Vendor", "Role Origin Agent", "Role Coloader", "Role Iata", "Role Cfs", "Role Both"} <= vend and {"Role Customer", "Role Both"} <= cli and "Role Vendor" not in cli
    assert client.get("/api/customers?role=coloader", headers=h).json()[0]["name"] == "Role Coloader"
