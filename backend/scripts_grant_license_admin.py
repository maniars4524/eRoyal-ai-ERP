"""One-off: give a user of the owner company (license SWF00001) the License Management authority.
Usage (from backend/): ENV_FILE=.env python scripts_grant_license_admin.py admin"""
import sys

from app.db import SessionLocal
from app.license_mgmt import grant_license_admin

with SessionLocal() as db:
    u = grant_license_admin(db, sys.argv[1])
    print("License Management granted to", u.username)
