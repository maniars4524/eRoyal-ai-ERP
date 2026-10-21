"""A charge in the Charge Master is applicable to AR (sales, the sell side), AP (purchases, the buy side) or Both. An AP-only charge cannot carry a sell rate and an AR-only charge cannot carry a buy rate."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MasterRecord


def applicability(db: Session, org_id: int) -> dict[str, str]:
    """charge name -> AR | AP | BOTH (a charge that is not in the master is BOTH)."""
    out = {}
    for name, data in db.execute(select(MasterRecord.name, MasterRecord.data).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "charge")):
        v = str((data or {}).get("ar_ap") or "Both").strip().upper()
        out[name] = v if v in ("AR", "AP") else "BOTH"
    return out


def check_sides(db: Session, org_id: int, entries) -> None:
    """entries: (charge_name, side 'SELL' | 'BUY', has_rate). Raises 422 when a rate sits on a side the charge is not applicable to."""
    appl = applicability(db, org_id)
    for name, side, has_rate in entries:
        if not has_rate:
            continue
        rule = appl.get((name or "").strip(), "BOTH")
        if side == "SELL" and rule == "AP":
            raise HTTPException(422, f"\"{name}\" is a purchase (AP) charge in the Charge Master, so it cannot carry a sell rate. Change its AR / AP setting in the Charge Master if it is also sold")
        if side == "BUY" and rule == "AR":
            raise HTTPException(422, f"\"{name}\" is a sales (AR) charge in the Charge Master, so it cannot carry a buy rate. Change its AR / AP setting in the Charge Master if it is also bought")
