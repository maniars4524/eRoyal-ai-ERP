"""Global Port Master load: ~17,600 UN/LOCODE ports and ~4,000 scheduled-service airports (see scripts/build_ports_data.py).
Idempotent: codes that already exist (including ports a user created or edited) are never overwritten.
Run:  python -m app.ports_import   (uses ENV_FILE like the app)"""
import csv
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Port

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "ports_global.csv"
MIN_EXPECTED = 100  # fewer ports than this means the master has never been loaded


def import_global(db: Session) -> dict:
    rows = []
    with open(DATA_FILE, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append({"code": r["code"], "unlocode": r["unlocode"] or None, "port_type": r["port_type"], "name": r["name"], "city": r["city"] or None, "country": r["country"], "status": "ACTIVE"})
    before = db.scalar(select(func.count()).select_from(Port)) or 0
    for i in range(0, len(rows), 2000):
        db.execute(pg_insert(Port).values(rows[i:i + 2000]).on_conflict_do_nothing(index_elements=["code"]))
    db.commit()
    after = db.scalar(select(func.count()).select_from(Port)) or 0
    return {"file_rows": len(rows), "created": after - before, "skipped": len(rows) - (after - before), "total": after}


def ensure_loaded(db: Session) -> dict | None:
    """Load the global ports when the master is (nearly) empty; used by the start-up seed."""
    if (db.scalar(select(func.count()).select_from(Port)) or 0) >= MIN_EXPECTED:
        return None
    return import_global(db)


if __name__ == "__main__":
    s = SessionLocal()
    try:
        print(import_global(s))
    finally:
        s.close()
