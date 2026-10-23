"""A new company starts with the owner company's Airline, Sea Line, Charge and HSN masters (additive); it can add its own on top."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import license_mgmt as lm
from app.db import SessionLocal
from app.main import app
from app.models import Airline, MasterRecord, Organization, ShippingLine

client = TestClient(app)


def test_new_company_gets_the_reference_masters(world, admin, monkeypatch):
    monkeypatch.setattr(lm, "OWNER_LICENSE", world["erp_license"].upper())
    tag = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        oid = world["erp_org"]
        db.add(Airline(organization_id=oid, code="ZZ" + tag[:2], name="Ref Air " + tag, status="ACTIVE"))
        db.add(ShippingLine(organization_id=oid, code="RL" + tag[:3], name="Ref Line " + tag, status="ACTIVE"))
        db.add(MasterRecord(organization_id=oid, master_type="charge", code="REF" + tag, name="Ref Charge " + tag, status="ACTIVE", data={"hsn_code": "996511"}))
        db.commit()
    dev = client.post("/api/auth/login", json={"identifier": world["dev"], "password": "Portal-Test-Passw0rd!", "portal": "developer"}).json()["access_token"]
    made = client.post("/api/organizations", json={"code": "RM" + tag, "name": "RefMasters " + tag, "admin_email": f"rm{tag}@t.test", "admin_username": "rm" + tag.lower(), "admin_password": "Tenant-Admin-Pass-1!", "admin_first_name": "R"},
                       headers={"Authorization": "Bearer " + dev})
    assert made.status_code == 201, made.text
    new_id = made.json()["organization"]["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Airline).where(Airline.organization_id == new_id, Airline.name == "Ref Air " + tag)) == 1
        assert db.scalar(select(func.count()).select_from(ShippingLine).where(ShippingLine.organization_id == new_id, ShippingLine.name == "Ref Line " + tag)) == 1
        assert db.scalar(select(func.count()).select_from(MasterRecord).where(MasterRecord.organization_id == new_id, MasterRecord.master_type == "charge", MasterRecord.code == "REF" + tag)) == 1
    with SessionLocal() as db:        # the test company references the developer user: remove it so the fixtures can clean up
        db.delete(db.get(Organization, new_id))
        db.commit()
