"""One-off: load the company airline list (data/airlines.csv) into one company. Usage (from backend/): ENV_FILE=.env python scripts_load_airlines.py ESWAYDEMO
Placeholder and repeated rows are skipped, airlines already there are never duplicated. Nothing is linked to an organization unless one with the same name exists."""
import csv
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization, User
from app.network_api import _import

code = sys.argv[1]
with SessionLocal() as db:
    org = db.scalar(select(Organization).where(Organization.code == code))
    if org is None:
        raise SystemExit("No such company: " + code)
    user = db.scalar(select(User).where(User.organization_id == org.id).order_by(User.id))
    rows = list(csv.DictReader(Path("data/airlines.csv").open(encoding="utf-8", newline="")))
    r = _import(db, org.id, user, "airlines", rows, True)
    print(code, {k: r[k] for k in ("new", "skipped", "errors", "padded", "linked", "applied")})
    for x in r["rows"]:
        if x["status"] != "NEW":
            print("  row", x["row"], x["status"], x["name"], "-", x["message"])
