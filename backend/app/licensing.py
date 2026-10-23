from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Organization

LICENSE_PREFIX = "SWF"
LICENSE_DIGITS = 5


def next_license_number(db: Session) -> str:
    """The next company license: SWF00001, SWF00002, ... (one more than the highest SWF number in use)."""
    existing = db.scalars(select(Organization.license_number).where(Organization.license_number.like(f"{LICENSE_PREFIX}%"))).all()
    highest = 0
    for value in existing:
        suffix = value[len(LICENSE_PREFIX):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{LICENSE_PREFIX}{highest + 1:0{LICENSE_DIGITS}d}"
