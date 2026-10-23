"""Copy the reference masters (Airline, Sea Line, Charge, HSN / SAC) of one company into another (additive: what already exists, by code, is left alone).
Usage (from backend/):  ENV_FILE=.env python scripts_copy_masters.py ESWAYDEMO DEMO-COMPANY"""
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization
from app.reference_masters import copy_reference_masters


def main(src_code, dst_code):
    with SessionLocal() as db:
        src = db.scalar(select(Organization).where(Organization.code == src_code))
        dst = db.scalar(select(Organization).where(Organization.code == dst_code))
        if not src or not dst:
            raise SystemExit("Unknown company code")
        for label, (made, skipped) in copy_reference_masters(db, src, dst).items():
            print(f"{label}: {made} copied, {skipped} already there")
        db.commit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
