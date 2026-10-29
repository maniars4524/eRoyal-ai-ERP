"""Basis-based charge calculation: ONE service used by the quotation screen, the job cost screen, the job charges save and the reports, so the same Basis never gives different numbers.

A charge line is  amount = quantity x rate.  The Basis decides where the quantity comes from:
  Per Shipment            1
  Per Package             number of packages
  By Gross Wt             gross weight (kg)
  By Chg Wt               chargeable weight (kg): Air = greater of gross and volumetric (rounded up to 0.5 kg, as the job already does); other modes = greater of gross kg and CBM x 1000
  By Volume               volume (CBM); total of L x W x H x packages / 1,000,000 when only dimensions are known
  % of Freight            quantity = freight base / 100 and the rate is the percentage, so amount = base x % / 100
  % of Other Charges      same, on the other (non-freight, non-percentage) charges of the same side
  Per Vehicle             number of vehicles
  By Gross Wt/Volume      Air: greater of gross and volumetric kg;  Sea LCL: greater of gross weight in tonnes and CBM (W/M); other modes are refused (not mixed)
  By Net Wt               net weight (kg)
Cost (BUY) and Sell (SELL) lines are calculated independently: a percentage takes its base from the lines of its own side only.
All arithmetic is Decimal; amounts are rounded to 2 places, quantities to 3."""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from . import config

PER_SHIPMENT, PER_PACKAGE, BY_GROSS, BY_CHG, BY_VOLUME = "Per Shipment", "Per Package", "By Gross Wt", "By Chg Wt", "By Volume"
PCT_FREIGHT, PCT_OTHER, PER_VEHICLE, BY_GW_VOL, BY_NET = "% of Freight", "% of Other Charges", "Per Vehicle", "By Gross Wt/Volume", "By Net Wt"
BASES = [PER_SHIPMENT, PER_PACKAGE, BY_GROSS, BY_CHG, BY_VOLUME, PCT_FREIGHT, PCT_OTHER, PER_VEHICLE, BY_GW_VOL, BY_NET]
UNITS = {PER_SHIPMENT: "SHP", PER_PACKAGE: "PKG", BY_GROSS: "KG", BY_CHG: "KG", BY_VOLUME: "CBM", PCT_FREIGHT: "% of freight", PCT_OTHER: "% of other charges", PER_VEHICLE: "VEH", BY_GW_VOL: "W/M or KG", BY_NET: "KG"}
PERCENT_BASES = (PCT_FREIGHT, PCT_OTHER)
ZERO, ONE, HUNDRED, THOUSAND = Decimal(0), Decimal(1), Decimal(100), Decimal(1000)
Q3, Q2 = Decimal("0.001"), Decimal("0.01")


class BasisError(ValueError):
    """A measurement or rate that cannot be used; the message is shown to the user."""


def dec(v: Any, what: str = "value", allow_blank: bool = True) -> Decimal:
    if v is None or str(v).strip() == "":
        if allow_blank:
            return ZERO
        raise BasisError(f"{what} is required")
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except InvalidOperation:
        raise BasisError(f"{what} is not a number")
    if not d.is_finite():
        raise BasisError(f"{what} is not a number")
    if d < 0:
        raise BasisError(f"{what} cannot be negative")
    return d


def q3(d: Decimal) -> Decimal:
    return d.quantize(Q3, rounding=ROUND_HALF_UP)


def q2(d: Decimal) -> Decimal:
    return d.quantize(Q2, rounding=ROUND_HALF_UP)


def divisor_default() -> Decimal:
    return Decimal(str(getattr(config, "AIR_VOLUMETRIC_DIVISOR", 6000)))


class Measures:
    """The shipment's quantities. Weights in kg, volume in CBM, dimension rows in cm: {l, w, h, packages}."""

    def __init__(self, mode: str = "Sea", shipment_type: str = "", packages: Any = None, gross_kg: Any = None, net_kg: Any = None, volume_cbm: Any = None,
                 chargeable_kg: Any = None, vehicles: Any = None, dims: list[dict] | None = None, divisor: Any = None):
        self.mode = (mode or "Sea").strip()
        self.shipment_type = (shipment_type or "").strip().upper()
        self.packages = dec(packages, "Number of packages")
        self.gross_kg = dec(gross_kg, "Gross weight")
        self.net_kg = dec(net_kg, "Net weight")
        self.vehicles = dec(vehicles, "Number of vehicles")
        self.explicit_volume = dec(volume_cbm, "Volume")
        self.explicit_chargeable = dec(chargeable_kg, "Chargeable weight")
        self.divisor = dec(divisor, "Volumetric divisor") or divisor_default()
        if self.divisor <= 0:
            raise BasisError("The volumetric divisor must be greater than zero")
        self.dims = []
        for i, r in enumerate(dims or [], start=1):
            l, w, h, p = dec(r.get("l"), f"Length (row {i})"), dec(r.get("w"), f"Width (row {i})"), dec(r.get("h"), f"Height (row {i})"), dec(r.get("packages") or r.get("pkg") or 1, f"Packages (row {i})")
            if (l or w or h) and not (l > 0 and w > 0 and h > 0):
                raise BasisError(f"Dimension row {i} needs length, width and height")
            if l > 0 and w > 0 and h > 0:
                self.dims.append((l, w, h, p or ONE))

    @property
    def is_air(self) -> bool:
        return self.mode.lower() == "air"

    @property
    def is_sea_lcl(self) -> bool:
        return self.mode.lower() == "sea" and (self.shipment_type == "LCL" or (self.shipment_type == "" and False))

    @property
    def volumetric_kg(self) -> Decimal:
        return sum((l * w * h * p / self.divisor for l, w, h, p in self.dims), ZERO)

    @property
    def dims_cbm(self) -> Decimal:
        return sum((l * w * h * p / Decimal(1_000_000) for l, w, h, p in self.dims), ZERO)

    @property
    def volume_cbm(self) -> Decimal:
        return self.explicit_volume or self.dims_cbm

    @property
    def air_volumetric_kg(self) -> Decimal:
        if self.dims:
            return self.volumetric_kg
        return self.explicit_volume * Decimal(1_000_000) / self.divisor if self.explicit_volume else ZERO

    @property
    def chargeable_kg(self) -> Decimal:
        if self.explicit_chargeable:
            return self.explicit_chargeable
        if self.is_air:
            kg = max(self.gross_kg, self.air_volumetric_kg)
            return Decimal(str(math.ceil(round(float(kg) * 2, 6)) / 2)) if kg > 0 else ZERO       # the job's own rule: rounded up to the next 0.5 kg
        return max(self.gross_kg, self.volume_cbm * THOUSAND)

    def snapshot(self) -> dict:
        return {"mode": self.mode, "shipment_type": self.shipment_type, "packages": str(self.packages), "gross_kg": str(self.gross_kg), "net_kg": str(self.net_kg), "volume_cbm": str(q3(self.volume_cbm)),
                "volumetric_kg": str(q3(self.air_volumetric_kg)), "chargeable_kg": str(q3(self.chargeable_kg)), "vehicles": str(self.vehicles), "divisor": str(self.divisor),
                "weight_tonnes": str(q3(self.gross_kg / THOUSAND))}


def quantity(basis: str, m: Measures) -> tuple[Decimal, str]:
    """The quantity of a measure-based Basis. Returns (quantity, unit)."""
    if basis == PER_SHIPMENT:
        return q3(ONE), UNITS[basis]
    if basis == PER_PACKAGE:
        if m.packages <= 0:
            raise BasisError("Enter the number of packages for this shipment")
        return q3(m.packages), UNITS[basis]
    if basis == BY_GROSS:
        if m.gross_kg <= 0:
            raise BasisError("Enter the gross weight of this shipment")
        return q3(m.gross_kg), UNITS[basis]
    if basis == BY_NET:
        if m.net_kg <= 0:
            raise BasisError("Enter the net weight of this shipment")
        return q3(m.net_kg), UNITS[basis]
    if basis == BY_CHG:
        kg = m.chargeable_kg
        if kg <= 0:
            raise BasisError("Enter the gross weight or the package dimensions to get a chargeable weight")
        return q3(kg), UNITS[basis]
    if basis == BY_VOLUME:
        if m.volume_cbm <= 0:
            raise BasisError("Enter the volume (CBM) or the package dimensions")
        return q3(m.volume_cbm), "CBM"
    if basis == PER_VEHICLE:
        if m.vehicles <= 0:
            raise BasisError("Enter the number of vehicles")
        return q3(m.vehicles), UNITS[basis]
    if basis == BY_GW_VOL:
        if m.is_air:
            kg = max(m.gross_kg, m.air_volumetric_kg)
            if kg <= 0:
                raise BasisError("Enter the gross weight or the package dimensions")
            return q3(kg), "KG"
        if m.mode.lower() == "sea" and m.shipment_type == "LCL":
            wm = max(m.gross_kg / THOUSAND, m.volume_cbm)
            if wm <= 0:
                raise BasisError("Enter the gross weight and the volume (CBM) of this LCL shipment")
            return q3(wm), "W/M"
        raise BasisError("By Gross Wt/Volume applies to Air and to Sea LCL shipments only")
    raise BasisError(f"'{basis}' is not a calculated Basis")


def is_freight(name: str = "", category: str = "") -> bool:
    c, n = (category or "").strip().lower(), (name or "").strip().lower()
    return c == "freight" or (not c and "freight" in n and "surcharge" not in n)


def calc_lines(lines: list[dict], m: Measures) -> dict:
    """lines: {id, side BUY|SELL, basis, rate, qty (manual), name, category, ccy, exch}. Every line gets quantity, unit, amount (own currency) and amount_inr;
    a percentage line takes its base from the other lines of the SAME side, never from itself or from another percentage line."""
    out: dict[str, dict] = {}
    plain, pct = [], []
    for i, ln in enumerate(lines):
        key = str(ln.get("id", i))
        exch = dec(ln.get("exch") or 1, "Exchange rate") or ONE
        base = {"id": key, "side": (ln.get("side") or "SELL").upper(), "basis": ln.get("basis") or "", "rate": str(dec(ln.get("rate"), "Rate")), "ccy": ln.get("ccy") or "INR", "exch": str(exch),
                "freight": is_freight(ln.get("name"), ln.get("category")), "error": None}
        out[key] = base
        b = base["basis"]
        try:
            rate = dec(ln.get("rate"), "Rate")
            if b in PERCENT_BASES:
                if rate > HUNDRED * 10:
                    raise BasisError("A percentage above 1,000 is not accepted")
                pct.append((ln, base, rate, exch))
                continue
            if b == PER_SHIPMENT:                                   # one by default; a quantity already entered for it (older jobs use it) is kept
                entered = ln.get("qty")
                qty, unit = q3(dec(entered, "Quantity") if entered not in (None, "") and dec(entered, "Quantity") > 0 else ONE), UNITS[b]
            elif b in BASES:
                qty, unit = quantity(b, m)
            else:                                                      # an existing basis (Container, KG ...): the quantity entered stays
                qty, unit = dec(ln.get("qty") if ln.get("qty") not in (None, "") else 1, "Quantity"), ""
            base.update({"quantity": str(qty), "unit": unit, "amount": str(q2(qty * rate)), "amount_inr": str(q2(qty * rate * exch))})
            plain.append(base)
        except BasisError as e:
            base.update({"error": str(e), "quantity": "0", "unit": UNITS.get(b, ""), "amount": "0.00", "amount_inr": "0.00"})
    for ln, base, rate, exch in pct:
        side = base["side"]
        mine = [x for x in plain if x["side"] == side]
        if base["basis"] == PCT_FREIGHT:
            pool, what = [x for x in mine if x["freight"]], "freight"
        else:
            pool, what = [x for x in mine if not x["freight"]], "other charges"
        total_inr = sum((Decimal(x["amount_inr"]) for x in pool), ZERO)
        if not pool or total_inr <= 0:
            base.update({"error": f"There is no {what} on the {'cost' if side == 'BUY' else 'sell'} side to take a percentage of yet", "quantity": "0", "unit": UNITS[base["basis"]], "amount": "0.00", "amount_inr": "0.00"})
            continue
        amount_inr = q2(total_inr * rate / HUNDRED)
        amount = q2(amount_inr / exch)
        # stored the way every other line is: quantity x rate, quantity = base / 100 (in the line's own currency), rate = the percentage
        base.update({"quantity": str(q3(total_inr / exch / HUNDRED)), "unit": UNITS[base["basis"]], "amount": str(amount), "amount_inr": str(amount_inr), "base_inr": str(q2(total_inr))})
    totals = {}
    for side in ("BUY", "SELL"):
        totals[side] = str(sum((Decimal(x["amount_inr"]) for x in out.values() if x["side"] == side and not x["error"]), ZERO))
    buy, sell = Decimal(totals["BUY"]), Decimal(totals["SELL"])
    profit = sell - buy
    return {"lines": list(out.values()), "totals": {"cost": str(q2(buy)), "sell": str(q2(sell)), "gross_profit": str(q2(profit)), "margin_pct": str(q2(profit / sell * HUNDRED)) if sell > 0 else None,
            "margin_pct_precise": str(profit / sell * HUNDRED) if sell > 0 else None}, "measures": m.snapshot(), "errors": [x["error"] for x in out.values() if x["error"]]}
