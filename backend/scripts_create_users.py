"""One-off: create admin users in a company. Usage (from backend/): ENV_FILE=.env python scripts_create_users.py SWF00002 abirami saravana naveen
The password of each user is set to the user name (the user's own choice); users can change it from their profile."""
import sys

from sqlalchemy import func, select

from app.auth_api import record_audit
from app.db import SessionLocal
from app.models import Organization, User
from app.roles import ensure_role
from app.security import hash_password

lic, names = sys.argv[1], sys.argv[2:]
with SessionLocal() as db:
    org = db.scalar(select(Organization).where(Organization.license_number == lic))
    if org is None:
        raise SystemExit("No company with license " + lic)
    role = ensure_role(db, org.id, "ERP_ADMIN")
    for name in names:
        email = f"{name}@{lic.lower()}.local"
        if db.scalar(select(User.id).where((func.lower(User.username) == name) | (func.lower(User.email) == email))):
            print(name, "already exists - skipped")
            continue
        u = User(organization_id=org.id, username=name, email=email, password_hash=hash_password(name), first_name=name.capitalize(), last_name="", status="ACTIVE", profile={})
        u.roles.append(role)
        db.add(u)
        db.flush()
        record_audit(db, "USER_CREATED", "USER", None, org.id, "User", str(u.id), {"username": name, "role": "ERP_ADMIN"})
        print("created", name, "in", org.name, "(" + lic + ")")
    db.commit()
