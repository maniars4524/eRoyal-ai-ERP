"""The one cargo calculation engine: dimensions -> CBM, volumetric weight, actual weight, chargeable weight (Air) and chargeable revenue ton (Sea LCL).

Every screen and module (job dimensions, quotation, job costing, reports) calls this; the server's result is the authoritative one, the browser only previews.
All arithmetic is Decimal and nothing is rounded per row: totals are summed from the exact values and the rounding rule is applied once, at the end, to the figure that is rated.
Nothing here touches accounting. FCL shipments get their cargo figures for the record but no chargeable weight / RT: container-based charging stays as it is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from . import config

LENGTH_CM = {"CM": Decimal(1), "MM": Decimal("0.1"), "M": Decimal(100), "INCH": Decimal("2.54"), "FEET": Decimal("30.48")}
WEIGHT_KG = {"KG": Decimal(1), "TON": Decimal(1000)}            # TON = metric tonne
ROUNDING = ("UP_HALF", "UP_ONE", "NEAREST_HALF", "NONE")         # for chargeable weight (kg)
RT_ROUNDING = ("NONE", "UP_TENTH", "UP_HALF", "UP_ONE")           # for chargeable revenue ton
ZERO, ONE, THOUSAND, MILLION = Decimal(0), Decimal(1), Decimal(1000), Decimal(1_000_000)
Q3 = Decimal("0.001")


class CargoError(ValueError):
    """A cargo entry that cannot be calculated; the message is meant for the user."""


@dataclass
class Config:
    """How a shipment is rated. The default comes from the company settings; carrier / service / customer rules replace it (admin screen, later phase)."""
    divisor: Decimal = Decimal(6000)
    rounding: str = "UP_HALF"                 # chargeable kg
    rt_rounding: str = "NONE"                 # chargeable revenue ton
    wm_kg_per_cbm: Decimal = Decimal(1000)    # sea LCL: 1 CBM is rated against this many kg
    min_chargeable_kg: Decimal = ZERO
    min_rt: Decimal = ZERO
    reference: str = "default"                # which configuration was used, kept with the result
    express: bool = False

    def validate(self) -> "Config":
        if self.divisor <= 0:
            raise CargoError("The volumetric divisor must be greater than zero")
        if self.rounding not in ROUNDING:
            raise CargoError("Unknown chargeable weight rounding: " + str(self.rounding))
        if self.rt_rounding not in RT_ROUNDING:
            raise CargoError("Unknown revenue ton rounding: " + str(self.rt_rounding))
        if self.wm_kg_per_cbm <= 0:
            raise CargoError("The W/M weight-per-CBM figure must be greater than zero")
        return self


def default_config(express: bool = False) -> Config:
    base = Decimal(str(getattr(config, "AIR_VOLUMETRIC_DIVISOR", 6000)))
    return Config(divisor=Decimal(5000) if express else base, express=express, reference="express default" if express else "company default").validate()


def dec(value: Any, what: str, positive: bool = False, allow_blank: bool = True) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_blank:
            return ZERO
        raise CargoError(f"{what} is required")
    try:
        n = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise CargoError(f"{what} is not a number")
    if not n.is_finite():
        raise CargoError(f"{what} is not a number")
    if n < 0:
        raise CargoError(f"{what} cannot be negative")
    if positive and n <= 0:
        raise CargoError(f"{what} must be greater than zero")
    return n


def q3(d: Decimal) -> Decimal:
    return d.quantize(Q3, rounding=ROUND_HALF_UP)


def round_chargeable_kg(kg: Decimal, rule: str = "UP_HALF") -> Decimal:
    """UP_HALF: up to the next 0.5 kg (the ERP's existing air rule). UP_ONE: up to the next whole kg. NEAREST_HALF: to the nearest 0.5. NONE: unrounded."""
    if kg <= 0:
        return ZERO
    if rule == "UP_HALF":
        return Decimal(str(math.ceil(round(float(kg) * 2, 6)) / 2))
    if rule == "UP_ONE":
        return Decimal(math.ceil(round(float(kg), 6)))
    if rule == "NEAREST_HALF":
        return (kg * 2).quantize(ONE, rounding=ROUND_HALF_UP) / 2
    return kg


def round_rt(rt: Decimal, rule: str = "NONE") -> Decimal:
    if rt <= 0 or rule == "NONE":
        return rt
    step = {"UP_TENTH": Decimal("0.1"), "UP_HALF": Decimal("0.5"), "UP_ONE": ONE}[rule]
    return Decimal(math.ceil(round(float(rt / step), 6))) * step


# ---------------------------------------------------------------- the pieces other modules reuse (so there is one formula)
def row_cbm_cm(l_cm: Decimal, w_cm: Decimal, h_cm: Decimal, packages: Decimal) -> Decimal:
    return l_cm * w_cm * h_cm * packages / MILLION


def row_volumetric_kg_cm(l_cm: Decimal, w_cm: Decimal, h_cm: Decimal, packages: Decimal, divisor: Decimal) -> Decimal:
    return l_cm * w_cm * h_cm * packages / divisor


def total_volumetric_kg(dims_cm: list[tuple], divisor: Decimal) -> Decimal:
    """dims_cm: (l, w, h, packages) in centimetres."""
    return sum((row_volumetric_kg_cm(l, w, h, p, divisor) for l, w, h, p in dims_cm), ZERO)


def total_cbm(dims_cm: list[tuple]) -> Decimal:
    return sum((row_cbm_cm(l, w, h, p) for l, w, h, p in dims_cm), ZERO)


# ---------------------------------------------------------------- rows
@dataclass
class Row:
    packages: Decimal
    length: Decimal
    width: Decimal
    height: Decimal
    unit: str = "CM"
    gross: Decimal = ZERO
    gross_unit: str = "KG"
    weight_basis: str = "TOTAL"        # TOTAL: the row's weight is for all its packages. PER_PACKAGE: older jobs, the weight is of one package
    package_type: str = ""
    remarks: str = ""
    has_dims: bool = field(default=True)


def parse_rows(raw: list[dict] | None, default_weight_basis: str = "TOTAL") -> list[Row]:
    """Rows as the screens send them. A row may carry only weight (no dimensions): it then adds weight but no volume. A row with some dimensions needs all three."""
    out: list[Row] = []
    for i, r in enumerate(raw or [], start=1):
        unit = str(r.get("unit") or "CM").upper()
        if unit not in LENGTH_CM:
            raise CargoError(f"Row {i}: unknown dimension unit {unit} (use CM, MM, M, INCH or FEET)")
        wunit = str(r.get("gross_unit") or r.get("weight_unit") or "KG").upper()
        if wunit not in WEIGHT_KG:
            raise CargoError(f"Row {i}: unknown weight unit {wunit} (use KG or TON)")
        raw_pk = r.get("packages", r.get("pkg"))
        l, w, h = dec(r.get("length", r.get("l")), f"Row {i}: length"), dec(r.get("width", r.get("w")), f"Row {i}: width"), dec(r.get("height", r.get("h")), f"Row {i}: height")
        gross = dec(r.get("gross", r.get("gross_weight", r.get("awt"))), f"Row {i}: gross weight")
        any_dim = l > 0 or w > 0 or h > 0
        if any_dim and not (l > 0 and w > 0 and h > 0):
            raise CargoError(f"Row {i}: length, width and height must all be greater than zero")
        blank_pk = raw_pk is None or (isinstance(raw_pk, str) and not raw_pk.strip())
        if not any_dim and gross <= 0 and blank_pk:
            continue                                      # an empty row is ignored, never calculated as zero freight
        pk = ONE if blank_pk else dec(raw_pk, f"Row {i}: number of packages")
        if pk <= 0:
            raise CargoError(f"Row {i}: number of packages must be greater than zero")
        basis = str(r.get("weight_basis") or default_weight_basis).upper()
        if basis not in ("TOTAL", "PER_PACKAGE"):
            raise CargoError(f"Row {i}: weight basis must be TOTAL or PER_PACKAGE")
        out.append(Row(packages=pk, length=l, width=w, height=h, unit=unit, gross=gross, gross_unit=wunit, weight_basis=basis, package_type=str(r.get("package_type") or ""), remarks=str(r.get("remarks") or ""), has_dims=any_dim))
    return out


def calc_row(r: Row, cfg: Config) -> dict:
    """Exact values of one row (not rounded; the caller rounds for display only)."""
    f = LENGTH_CM[r.unit]
    l, w, h = r.length * f, r.width * f, r.height * f
    cbm = row_cbm_cm(l, w, h, r.packages) if r.has_dims else ZERO
    vol = row_volumetric_kg_cm(l, w, h, r.packages, cfg.divisor) if r.has_dims else ZERO
    gross_kg = r.gross * WEIGHT_KG[r.gross_unit] * (r.packages if r.weight_basis == "PER_PACKAGE" else ONE)
    return {"cbm": cbm, "volumetric_kg": vol, "gross_kg": gross_kg}


def compute(rows: list[dict] | list[Row] | None, mode: str = "Air", shipment_type: str = "", cfg: Config | None = None, shipment_gross_kg: Any = None, default_weight_basis: str = "TOTAL",
            reference: dict | None = None) -> dict:
    """Totals and the chargeable figures for one shipment. Raises CargoError for anything that cannot be calculated (never a silent zero)."""
    cfg = (cfg or default_config()).validate()
    parsed = rows if rows and isinstance(rows[0], Row) else parse_rows(rows, default_weight_basis)       # type: ignore[arg-type]
    mode_n, stype = (mode or "Air").strip().title(), (shipment_type or "").strip().upper()
    per_row, cbm, vol, row_gross, pk = [], ZERO, ZERO, ZERO, ZERO
    for i, r in enumerate(parsed, start=1):
        c = calc_row(r, cfg)
        cbm += c["cbm"]
        vol += c["volumetric_kg"]
        row_gross += c["gross_kg"]
        pk += r.packages
        per_row.append({"serial": i, "packages": str(r.packages), "package_type": r.package_type, "length": str(r.length), "width": str(r.width), "height": str(r.height), "unit": r.unit,
                        "gross": str(r.gross), "gross_unit": r.gross_unit, "weight_basis": r.weight_basis, "cbm": str(q3(c["cbm"])), "volumetric_kg": str(q3(c["volumetric_kg"])),
                        "gross_kg": str(q3(c["gross_kg"])), "remarks": r.remarks})
    shipment_kg = dec(shipment_gross_kg, "Gross weight")
    gross = row_gross if row_gross > 0 else shipment_kg                  # row weights, or the shipment-level weight when the rows carry none; never both (no double counting)
    out: dict[str, Any] = {"mode": mode_n, "shipment_type": stype, "rows": per_row, "packages": str(pk), "cbm": str(q3(cbm)), "gross_kg": str(q3(gross)), "volumetric_kg": str(q3(vol)), "weight_mt": str(q3(gross / THOUSAND)),
                           "chargeable_kg": None, "chargeable_rt": None, "basis": None, "applicable": [], "warnings": []}
    if mode_n == "Air":
        out["applicable"] = ["chargeable_kg"]
        if gross <= 0 and vol <= 0:
            out["warnings"].append("Enter the gross weight or the package dimensions: there is nothing to rate yet")
        else:
            kg = max(gross, vol)
            kg = round_chargeable_kg(kg, cfg.rounding)
            kg = max(kg, cfg.min_chargeable_kg)
            out["chargeable_kg"], out["basis"] = str(q3(kg)), "volumetric" if vol > gross else "actual"
    elif mode_n == "Sea" and stype == "LCL":
        out["applicable"] = ["chargeable_rt"]
        wm_weight = gross / cfg.wm_kg_per_cbm
        if cbm <= 0 and gross <= 0:
            out["warnings"].append("Enter the gross weight and the dimensions (or CBM): there is nothing to rate yet")
        else:
            if cbm <= 0:
                out["warnings"].append("No dimensions entered: the revenue ton is based on weight only")
            if gross <= 0:
                out["warnings"].append("No gross weight entered: the revenue ton is based on volume only")
            rt = max(round_rt(max(cbm, wm_weight), cfg.rt_rounding), cfg.min_rt)
            out["chargeable_rt"], out["basis"] = str(q3(rt)), "weight" if wm_weight > cbm else "volume"
    elif mode_n == "Sea" and stype in ("FCL", ""):
        out["warnings"].append("FCL is charged per container: no chargeable weight or revenue ton is calculated")
    out["method"] = {"divisor": str(cfg.divisor), "rounding": cfg.rounding, "rt_rounding": cfg.rt_rounding, "wm_kg_per_cbm": str(cfg.wm_kg_per_cbm), "min_chargeable_kg": str(cfg.min_chargeable_kg), "min_rt": str(cfg.min_rt),
                     "configuration": cfg.reference, "express": cfg.express, "formula_air": f"L x W x H (cm) x packages / {cfg.divisor}", "formula_lcl": f"RT = greater of CBM and gross kg / {cfg.wm_kg_per_cbm}",
                     "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **(reference or {})}
    return out
