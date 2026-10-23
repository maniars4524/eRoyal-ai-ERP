import re
import uuid

from app.db import SessionLocal
from app.licensing import next_license_number
from app.models import Organization


def test_licenses_run_swf00001_swf00002_and_continue_from_the_highest(world):
    with SessionLocal() as db:
        first = next_license_number(db)
        assert re.fullmatch(r"SWF\d{5}", first)
        made = []
        try:
            for _ in range(2):
                lic = next_license_number(db)
                org = Organization(code="LIC" + uuid.uuid4().hex[:6].upper(), license_number=lic, name="Licence test", status="ACTIVE")
                db.add(org)
                db.flush()
                made.append(org)
            assert int(made[1].license_number[3:]) == int(made[0].license_number[3:]) + 1 == int(first[3:]) + 1
            assert next_license_number(db) == f"SWF{int(made[1].license_number[3:]) + 1:05d}"
        finally:
            for o in made:
                db.delete(o)
            db.commit()
