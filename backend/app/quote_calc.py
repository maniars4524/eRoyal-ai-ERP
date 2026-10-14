"""Quotation money rules (Decimal only; no floating point).

Conversion direction: ``exch`` is the number of BASE-currency units (INR) for ONE unit of the line's currency,
e.g. USD 850 at exch 83.5 = INR 70,975. Base-currency lines always use exch = 1.

Per line:  base      = units x unit rate          (container lines: sum of qty x rate per container type)
           converted = base x exch                (stored as ``amount`` in INR; ``amount_fcy`` keeps the original currency value)
           GST       = converted x GST %          (zero for non-taxable lines)
           total     = converted + GST

Margin % = (sell - buy) / sell x 100 and markup % = (sell - buy) / buy x 100 are different numbers and both are reported.
Margins are commercial: they are computed on amounts BEFORE GST (GST is a pass-through tax).
"""
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")
FOUR = Decimal("0.0001")
HUNDRED = Decimal(100)
ZERO = Decimal(0)


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(FOUR, rounding=ROUND_HALF_UP)


def calc_line(units: Decimal, rate: Decimal, ccy: str, exch: Decimal, taxable: bool, tax_pct: Decimal, breakup: list[dict] | None = None) -> dict:
    """Return every stored amount of one charge line."""
    if breakup:
        base = sum((q2(Decimal(str(b["qty"])) * Decimal(str(b["rate"]))) for b in breakup), ZERO)
        units = sum((Decimal(str(b["qty"])) for b in breakup), ZERO)
        rate = q4(base / units) if units else ZERO
    else:
        base = q2(units * rate)
    exch = Decimal(1) if ccy == "INR" else exch
    converted = q2(base * exch)
    pct = tax_pct if taxable else ZERO
    gst = q2(converted * pct / HUNDRED)
    return {"units": units, "unit_rate": rate, "exch": exch, "amount_fcy": base, "amount_inr": converted, "tax_pct": pct, "gst": gst, "total_inr": converted + gst}


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return q2(numerator / denominator * HUNDRED) if denominator else None


def cost_sheet(lines: list) -> dict:
    """Charge-wise and overall margin from SELL/BUY lines (objects with side, charge_name, carrier, amount, gst_amount)."""
    rows: "OrderedDict[str, dict]" = OrderedDict()
    carriers: "OrderedDict[str, dict]" = OrderedDict()
    sell_gst = buy_gst = ZERO
    for l in lines:
        name = (l.charge_name or "").strip() or "(unnamed charge)"
        row = rows.setdefault(name.lower(), {"charge": name, "sell": ZERO, "buy": ZERO})
        key = (l.carrier or "").strip() or "—"
        car = carriers.setdefault(key, {"carrier": key, "sell": ZERO, "buy": ZERO})
        if l.side == "SELL":
            row["sell"] += l.amount
            car["sell"] += l.amount
            sell_gst += l.gst_amount
        else:
            row["buy"] += l.amount
            car["buy"] += l.amount
            buy_gst += l.gst_amount
    total_sell = sum((r["sell"] for r in rows.values()), ZERO)
    total_buy = sum((r["buy"] for r in rows.values()), ZERO)
    margin = total_sell - total_buy
    result = "PROFIT" if margin > 0 else "LOSS" if margin < 0 else "BREAK_EVEN"
    return {
        "rows": [{"charge": r["charge"], "sell": str(r["sell"]), "buy": str(r["buy"]), "margin": str(r["sell"] - r["buy"]), "margin_pct": None if _pct(r["sell"] - r["buy"], r["sell"]) is None else str(_pct(r["sell"] - r["buy"], r["sell"]))} for r in rows.values()],
        "total_sell": str(total_sell), "total_buy": str(total_buy), "gross_margin": str(margin),
        "margin_pct": str(_pct(margin, total_sell) or ZERO), "markup_pct": None if _pct(margin, total_buy) is None else str(_pct(margin, total_buy)),
        "sell_gst": str(sell_gst), "buy_gst": str(buy_gst), "grand_total_sell": str(total_sell + sell_gst), "grand_total_buy": str(total_buy + buy_gst), "result": result,
        "by_carrier": [{"carrier": c["carrier"], "sell": str(c["sell"]), "buy": str(c["buy"]), "margin": str(c["sell"] - c["buy"]), "margin_pct": None if _pct(c["sell"] - c["buy"], c["sell"]) is None else str(_pct(c["sell"] - c["buy"], c["sell"]))} for c in carriers.values()],
    }


def charge_margin_pct(lines: list) -> dict[str, Decimal | None]:
    """Charge-level margin % keyed by lower-cased charge name (used for the per-line 'Margin Rate' column)."""
    sell: dict[str, Decimal] = {}
    buy: dict[str, Decimal] = {}
    for l in lines:
        key = (l.charge_name or "").strip().lower()
        (sell if l.side == "SELL" else buy)[key] = (sell if l.side == "SELL" else buy).get(key, ZERO) + l.amount
    return {k: (_pct(sell[k] - buy[k], sell[k]) if k in sell and k in buy else None) for k in set(sell) | set(buy)}
