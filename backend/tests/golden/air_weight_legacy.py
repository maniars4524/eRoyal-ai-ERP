"""Air freight weights. The package dimensions entered on the job give the volumetric weight; the CHARGEABLE weight is the greater of the shipment's actual (gross) weight and its volumetric weight,
rounded UP to the next 0.5 kg. It is calculated here, once, and used by the job screen, the Air Waybill, the job cost lines (charges on a chargeable-weight basis) and so the invoice and the purchase bill."""
from __future__ import annotations

import math
from typing import Any

LB_TO_KG = 0.45359237
FORMULAS = {
    "cm_kg": {"label": "(L × W × H cm) ÷ 6,000 per package × number of packages  → kg", "divisor": 6000},
    "in_lbs": {"label": "(L × W × H in) ÷ 166 per package × number of packages  → lb, shown in kg × 0.45359", "divisor": 166},
    "cbm": {"label": "(L × W × H cm) ÷ 1,000,000 = CBM × 167 kg per package × number of packages", "divisor": None},
}
CHARGEABLE_UOMS = ("chargeable",)          # a charge whose basis (UOM) contains this word is charged on the chargeable weight


def _n(v: Any) -> float:
    try:
        n = float(str(v).replace(",", "").strip())
        return n if n > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def round_up_half(kg: float) -> float:
    """Chargeable weights are rounded up to the next 0.5 kg."""
    return math.ceil(round(kg * 2, 6)) / 2 if kg > 0 else 0.0


def compute(dims: dict | None) -> dict:
    dims = dims or {}
    unit = dims.get("unit") if dims.get("unit") in FORMULAS else "cm_kg"
    rows, act, vol, pk = [], 0.0, 0.0, 0
    for r in dims.get("rows") or []:
        p = int(_n(r.get("pkg")) or 1)
        l, w, h, aw = _n(r.get("l")), _n(r.get("w")), _n(r.get("h")), _n(r.get("awt"))
        if unit == "cm_kg":
            v = l * w * h / 6000 * p
        elif unit == "in_lbs":
            v = l * w * h / 166 * p
        else:
            v = l * w * h / 1_000_000 * 167 * p
        a = aw * p
        f = LB_TO_KG if unit == "in_lbs" else 1.0
        rows.append({"packages": p, "actual_kg": round(a * f, 3), "volumetric_kg": round(v * f, 3)})
        act += a * f
        vol += v * f
        pk += p
    chargeable = round_up_half(max(act, vol))
    return {"unit": unit, "formula": FORMULAS[unit]["label"], "packages": pk, "actual_kg": round(act, 3), "volumetric_kg": round(vol, 3), "chargeable_kg": chargeable,
            "basis": "volumetric" if vol > act else "actual", "rows": rows, "rule": "greater of actual and volumetric weight, rounded up to the next 0.5 kg"}


def is_chargeable_uom(uom: str | None) -> bool:
    u = (uom or "").lower()
    return any(k in u for k in CHARGEABLE_UOMS)
