"""Placed users also only see their own leads, enquiries, quotations, bookings, CHA jobs, invoices, payments and vouchers - lists and direct ids."""
import uuid

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Organization, Permission, Role, Shipment, User
from app.security import hash_password
from tests.test_access_filters import hu
from tests.test_bookings import client, hdr

PERMS = ["shipment.read", "shipment.create", "shipment.update", "customer.read", "master.read", "quote.read", "quote.create", "booking.read", "booking.create", "billing.read", "billing.create", "payment.read"]


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def user(world, label):
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    role = Role(name=f"AR{label}{tag}", organization_id=org.id)
    role.permissions = list(db.scalars(select(Permission).where(Permission.code.in_(PERMS))))
    db.add(role)
    u = User(email=f"{label}-{tag}@users.test", username=f"{label}{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name=label, organization_id=org.id, status="ACTIVE")
    u.roles.append(role)
    db.add(u)
    db.commit()
    out = {"id": u.id, "username": u.username, "license": org.license_number}
    db.close()
    return out


@pytest.fixture(scope="module")
def w(world, h):
    tag = uuid.uuid4().hex[:5].upper()
    br = client.post("/api/access/branches", json={"code": f"R{tag}", "name": f"Rec {tag}", "region": "R1"}, headers=h).json()
    a, b, plain = user(world, "ra"), user(world, "rb"), user(world, "rp")
    for u, n in ((a, "A"), (b, "B")):
        assert client.put(f"/api/access/users/{u['id']}", json={"user_category": "SALESPERSON", "branch_id": br["id"], "salesperson_name": f"Sp{n}{tag}"}, headers=h).status_code == 200
    out = {"a": a, "b": b, "plain": plain, "tag": tag}
    for k in ("a", "b"):
        u, hd = out[k], hu(out[k])
        cust = client.post("/api/customers", json={"code": f"RC{k}{tag}", "name": f"Rcust {k} {tag}"}, headers=h).json()
        assert client.put(f"/api/access/customers/{cust['id']}/assignments", json={"user_ids": [u["id"]]}, headers=h).status_code == 200
        job = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "FCL", "pol": "INMAA", "pod": "BEANR", "consignee": f"c{k}{tag}", "customer_id": cust["id"]}, headers=h).json()
        quote = client.post("/api/quotations", json={"party_name": cust["name"], "customer_id": cust["id"], "mode": "Sea", "direction": "Export", "shipment_type": "FCL", "pol": "INMAA", "pod": "DEHAM",
                                                      "lines": [{"side": "SELL", "charge_name": "Ocean Freight", "carrier": "", "basis": "Per Container", "ccy": "USD", "exch": "83.5", "rate": "850", "qty": "2", "tax_pct": "0"}]}, headers=h).json()
        book = client.post("/api/bookings", json={"mode": "Sea", "direction": "Export", "shipment_type": "FCL", "party_name": cust["name"], "customer_id": cust["id"], "carrier": "MSC", "pol": "INMAA", "pod": "BEANR"}, headers=h).json()
        lead = client.put("/api/crm/lead", json={"ref": f"LD{k}{tag}", "customer_id": cust["id"], "company": cust["name"], "status": "New"}, headers=h).json()
        inv = client.post("/api/documents", json={"doc_type": "invoice", "customer_id": cust["id"], "party_name": cust["name"], "job_number": job["job_number"]}, headers=h).json()
        out[k + "x"] = {"cust": cust, "job": job, "quote": quote, "book": book, "lead": lead, "inv": inv}
    return out


def ids(rows):
    return {r["id"] for r in rows}


def test_unplaced_user_sees_all_records(w):
    hd = hu(w["plain"])
    assert {w["ax"]["quote"]["id"], w["bx"]["quote"]["id"]} <= ids(client.get("/api/quotations", headers=hd).json())
    assert {w["ax"]["inv"]["id"], w["bx"]["inv"]["id"]} <= ids(client.get("/api/documents", headers=hd).json())


def test_lists_show_only_own_records(w):
    hd, mine, other = hu(w["a"]), w["ax"], w["bx"]
    for url, key in (("/api/quotations", "quote"), ("/api/bookings", "book"), ("/api/documents", "inv")):
        seen = ids(client.get(url, headers=hd).json())
        assert mine[key]["id"] in seen and other[key]["id"] not in seen, url
    leads = ids(client.get("/api/crm/lead", headers=hd).json())
    assert mine["lead"]["id"] in leads and other["lead"]["id"] not in leads


def test_direct_ids_and_job_sub_pages_are_hidden(w):
    hd, mine, other = hu(w["a"]), w["ax"], w["bx"]
    for url in (f"/api/quotations/{other['quote']['id']}", f"/api/bookings/{other['book']['id']}", f"/api/documents/{other['inv']['id']}",
                f"/api/jobs/{other['job']['job_number']}/cha-jobs", f"/api/shipments/{other['job']['id']}/charges", f"/api/jobs/{other['job']['job_number']}/details"):
        assert client.get(url, headers=hd).status_code == 404, url
    for url in (f"/api/quotations/{mine['quote']['id']}", f"/api/bookings/{mine['book']['id']}", f"/api/documents/{mine['inv']['id']}"):
        assert client.get(url, headers=hd).status_code == 200, url
    assert client.post(f"/api/quotations/{other['quote']['id']}/duplicate", headers=hd).status_code == 404


def test_mis_and_dashboard_follow_the_same_scope(w, h):
    a = w["a"]
    mine = client.get("/api/mis/shipments", headers=hu(a))
    assert mine.status_code == 200, mine.text
    everyone = client.get("/api/mis/shipments", headers=h).json()
    assert mine.json()["total"] == 1 and everyone["total"] >= 2                         # a placed user's MIS counts only their own job; the administrator's counts all
    da = client.get("/api/dashboard/summary", headers=hu(a)).json()
    dall = client.get("/api/dashboard/summary", headers=h).json()
    assert str(da) != str(dall)                                                          # the dashboard figures differ: the placed user's are their own
    act = client.get("/api/dashboard/side?limit=40", headers=hu(a)).json()["activity"]
    assert len(act) <= len(client.get("/api/dashboard/side?limit=40", headers=h).json()["activity"])
