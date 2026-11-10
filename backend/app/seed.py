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
    ("license.manage", "Manage company licenses (License Management; owner company super admin only)", "license", "manage"),
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
    ("quote.edit_locked", "Edit a quotation after approval or issue (administrators)", "quote", "edit_locked"),
    ("quote.view_cost", "See buy costs and margins on quotations", "quote", "view_cost"),
    ("quote.rate_override", "Change the exchange rate on quotation lines", "quote", "rate_override"),
    ("credit.read", "View customer credit facilities and exposure", "credit", "read"),
    ("credit.request", "Apply for credit and request grace, extension or override", "credit", "request"),
    ("credit.approve", "Approve credit facilities and credit exceptions (management)", "credit", "approve"),
    ("credit.override", "Approve soft-block overrides", "credit", "override"),
    ("job.view_history", "View the job activity history and stage/closure history", "job", "view_history"),
    ("job.export_history", "Export the job activity history", "job", "export_history"),
    ("job.change_stage", "Manually change the job stage", "job", "change_stage"),
    ("job.close", "Close a job (manual closure)", "job", "close"),
    ("job.reopen", "Reopen a closed job", "job", "reopen"),
    ("bl.release_override", "Release an Original BL before full payment is confirmed (logged with a reason)", "bl", "release_override"),
    ("job.lock_override", "Take editing of a job from another user (logged with a reason; the previous editor is notified)", "job", "lock_override"),
    ("cha.exception", "Link a CHA job whose exporter, importer or ports differ from its freight job (with a reason)", "cha", "exception"),
    ("hbl.clause.custom", "Add a one-time clause to a House BL", "bl", "clause_custom"),
    ("hbl.clause.override", "Remove a mandatory HBL clause (logged with a reason)", "bl", "clause_override"),
    ("job.exceptional_close", "Approve an exceptional job closure", "job", "exceptional_close"),
    ("branch.manage", "Create and edit the company's own branches", "access", "branch_manage"),
    ("access.manage", "Place users in branches and teams, assign customers, set dashboards", "access", "manage"),
    ("dashboard.ceo", "Open the CEO Executive Dashboard (CEO_DASHBOARD_ACCESS)", "dashboard", "ceo"),
    ("sequence.view", "View Sequence Setting", "sequence", "view"),
    ("sequence.create", "Create numbering sequences", "sequence", "create"),
    ("sequence.edit", "Edit draft numbering sequences", "sequence", "edit"),
    ("sequence.suggest", "Use system sequence suggestions", "sequence", "suggest"),
    ("sequence.test", "Preview and test numbering sequences", "sequence", "test"),
    ("sequence.activate", "Activate numbering sequences", "sequence", "activate"),
    ("sequence.deactivate", "Deactivate or retire numbering sequences", "sequence", "deactivate"),
    ("sequence.adjust", "Adjust the next number of a sequence", "sequence", "adjust"),
    ("sequence.configure_financial", "Configure invoice and voucher sequences", "sequence", "configure_financial"),
    ("sequence.history", "View sequence history", "sequence", "history"),
    ("sequence.export", "Export sequence configuration and history", "sequence", "export"),
    ("currency.set_base", "Change the organisation base currency", "currency", "set_base"),
    ("fleet.read", "View vehicles, maintenance, tyres and fuel", "fleet", "read"),
    ("fleet.create", "Register vehicles, raise work orders, fit tyres, record fuel", "fleet", "create"),
    ("fleet.approve", "Close work orders, change vehicle status, sell / scrap vehicles", "fleet", "approve"),
    ("container.read", "View container inventory and movements", "container", "read"),
    ("container.create", "Create containers and draft movements", "container", "create"),
    ("container.approve", "Approve, reverse and cancel container movements; change grade / condition", "container", "approve"),
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
        if IS_PRODUCTION and password.lower() in {"admin", "password", "mani"}:
            raise RuntimeError("SUPER_ADMIN_PASSWORD must not be a well-known password when APP_ENV=production")
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


def sync_roles() -> None:
    from .roles import sync_template_roles

    db = SessionLocal()
    try:
        sync_template_roles(db)
    finally:
        db.close()


def seed_ports() -> None:
    from .ports_import import ensure_loaded

    db = SessionLocal()
    try:
        ensure_loaded(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed_super_admin()
    sync_roles()
    seed_ports()
    from . import std_masters

    with SessionLocal() as _db:
        std_masters.seed_all_orgs(_db)
