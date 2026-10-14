import os

from sqlalchemy import select

from .config import IS_PRODUCTION
from .db import SessionLocal
from .licensing import next_license_number
from .models import Organization, Permission, Role, User
from .security import hash_password

PERMISSIONS = [
    ("organization.read", "Read organizations", "organization", "read"),
    ("organization.create", "Create organizations", "organization", "create"),
    ("organization.update", "Update organizations", "organization", "update"),
    ("organization.activate", "Activate organizations", "organization", "activate"),
    ("user.read", "Read users", "user", "read"),
    ("user.create", "Create users", "user", "create"),
    ("user.update", "Update users", "user", "update"),
    ("customer.read", "Read customers", "customer", "read"),
    ("customer.create", "Create customers", "customer", "create"),
    ("customer.update", "Update customers", "customer", "update"),
    ("customer.delete", "Delete customers", "customer", "delete"),
    ("master.read", "Read master data", "master", "read"),
    ("master.create", "Create master data", "master", "create"),
    ("master.update", "Update master data", "master", "update"),
    ("master.delete", "Delete master data", "master", "delete"),
    ("billing.read", "View billing documents", "billing", "read"),
    ("billing.create", "Create/edit draft billing documents", "billing", "create"),
    ("billing.post", "Post billing documents", "billing", "post"),
    ("billing.cancel", "Cancel billing documents", "billing", "cancel"),
    ("payment.read", "View payments", "payment", "read"),
    ("payment.create", "Record payments", "payment", "create"),
    ("payment.cancel", "Cancel payments", "payment", "cancel"),
    ("quote.read", "View quotations", "quote", "read"),
    ("quote.create", "Create/edit draft quotations", "quote", "create"),
    ("quote.send", "Send, accept, reject and convert quotations", "quote", "send"),
    ("quote.approve", "Approve or reject submitted quotations", "quote", "approve"),
    ("quote.view_cost", "See buy costs and margins on quotations", "quote", "view_cost"),
    ("quote.rate_override", "Change the exchange rate on quotation lines", "quote", "rate_override"),
    ("credit.read", "View customer credit facilities and exposure", "credit", "read"),
    ("credit.request", "Apply for credit and request grace, extension or override", "credit", "request"),
    ("credit.approve", "Approve credit facilities and credit exceptions (management)", "credit", "approve"),
    ("credit.override", "Approve soft-block overrides", "credit", "override"),
    ("booking.read", "View bookings", "booking", "read"),
    ("booking.create", "Create/edit draft bookings", "booking", "create"),
    ("booking.confirm", "Confirm and cancel bookings", "booking", "confirm"),
    ("shipment.read", "Read shipment jobs", "shipment", "read"),
    ("shipment.create", "Create shipment jobs", "shipment", "create"),
    ("shipment.update", "Update shipment jobs", "shipment", "update"),
    ("shipment.delete", "Delete shipment jobs", "shipment", "delete"),
]


def seed_super_admin() -> None:
    db = SessionLocal()
    try:
        permissions = {}
        for code, name, module, action in PERMISSIONS:
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                permission = Permission(code=code, name=name, module=module, action=action)
                db.add(permission)
            permissions[code] = permission
        email = os.getenv("SUPER_ADMIN_EMAIL", "admin@esway.local")
        username = os.getenv("SUPER_ADMIN_USERNAME", "Admin")
        password = os.getenv("SUPER_ADMIN_PASSWORD", "admin")
        if IS_PRODUCTION and (len(password) < 12 or password.lower() in {"admin", "password", "mani"}):
            raise RuntimeError("SUPER_ADMIN_PASSWORD must be at least 12 characters when APP_ENV=production")
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, username=username, password_hash=hash_password(password), first_name="Platform", last_name="Administrator", is_super_admin=True)
            db.add(user)
        platform_org = db.scalar(select(Organization).where(Organization.code == "RTS-PLATFORM"))
        if not platform_org:
            platform_org = Organization(code="RTS-PLATFORM", license_number="RTS0001", name="eSway ERP 2.0", legal_name="eSway ERP 2.0", country="India", status="ACTIVE")
            db.add(platform_org)
            db.flush()
        db.commit()
    finally:
        db.close()


DEMO_CUSTOMERS = [
    ("DEMO-C001", "Sunrise Textiles Pvt Ltd", "Business", "India"),
    ("DEMO-C002", "Northwind Trading LLC", "Business", "United Arab Emirates"),
    ("DEMO-C003", "Pacific Rim Imports", "Business", "Singapore"),
]
DEMO_JOBS = [
    ("Sea", "Export", "FCL", "INMAA", "AEJEA", "Cotton garments", 0),
    ("Air", "Import", None, "SGSIN", "INMAA", "Electronic components", 2),
    ("Sea", "Import", "LCL", "CNSHA", "INMAA", "Machine parts", 1),
]


def seed_demo_portal() -> None:
    """Create the isolated demo tenant used by the /demo portal. Requires DEMO_PORTAL_PASSWORD; skipped otherwise."""
    password = os.getenv("DEMO_PORTAL_PASSWORD")
    if not password:
        return
    from .models import Customer, Shipment

    db = SessionLocal()
    try:
        permissions = {p.code: p for p in db.scalars(select(Permission))}
        org = db.scalar(select(Organization).where(Organization.code == "DEMO-COMPANY"))
        if not org:
            org = Organization(code="DEMO-COMPANY", license_number=next_license_number(db), name="eSway Demo Company", legal_name="eSway Demo Company", country="India", status="ACTIVE", is_demo=True)
            db.add(org)
            db.flush()
        org.is_demo = True
        role = db.scalar(select(Role).where(Role.organization_id == org.id, Role.name == "DEMO_USER"))
        if not role:
            role = Role(name="DEMO_USER", organization_id=org.id, description="Demo evaluator (no destructive actions)", status="ACTIVE")
            db.add(role)
        role.permissions = [permissions[c] for c in ("organization.read", "user.read", "master.read", "master.create", "master.update", "billing.read", "billing.create", "billing.post", "payment.read", "payment.create", "quote.read", "quote.create", "quote.send", "quote.approve", "quote.view_cost", "quote.rate_override", "booking.read", "booking.create", "booking.confirm", "credit.read", "credit.request", "credit.approve", "credit.override", "customer.read", "customer.create", "customer.update", "shipment.read", "shipment.create", "shipment.update") if c in permissions]
        email = os.getenv("DEMO_PORTAL_EMAIL", "demo@esway.local")
        username = os.getenv("DEMO_PORTAL_USERNAME", "demo")
        if not db.scalar(select(User).where((User.email == email) | (User.username == username))):
            user = User(email=email, username=username, password_hash=hash_password(password), first_name="Demo", last_name="User", organization_id=org.id, status="ACTIVE")
            user.roles.append(role)
            db.add(user)
        customers = []
        for code, name, category, country in DEMO_CUSTOMERS:
            customer = db.scalar(select(Customer).where(Customer.organization_id == org.id, Customer.code == code))
            if not customer:
                customer = Customer(organization_id=org.id, code=code, name=name, category=category, country=country, status="ACTIVE")
                db.add(customer)
            customers.append(customer)
        db.flush()
        if not db.scalar(select(Shipment.id).where(Shipment.organization_id == org.id)):
            from .shipments_api import _next_job_number

            for mode, direction, container, pol, pod, commodity, idx in DEMO_JOBS:
                db.add(Shipment(organization_id=org.id, job_number=_next_job_number(db, org.id, mode, direction), mode=mode, direction=direction, container_mode=container, pol=pol, pod=pod, commodity=commodity, customer_id=customers[idx].id, status="Open"))
                db.flush()
        db.commit()
    finally:
        db.close()


def sync_roles() -> None:
    from .roles import sync_template_roles

    db = SessionLocal()
    try:
        sync_template_roles(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed_super_admin()
    sync_roles()
    seed_demo_portal()
