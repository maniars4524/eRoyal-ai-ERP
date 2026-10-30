"""Helpers shared by the places that turn quotation / booking charges into job charges: HSN / SAC and GST from the Charge Master, and one head per charge when the same charge is quoted per container."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MasterRecord


def charge_info(db: Session, org_id: int) -> dict[str, tuple[str, str]]:
    """Charge name (lower case) -> (HSN / SAC code, GST %) from the active Charge Master entries."""
    out: dict[str, tuple[str, str]] = {}
    for rec in db.scalars(select(MasterRecord).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == "charge", MasterRecord.status == "ACTIVE")):
        d = rec.data or {}
        out[(rec.name or "").strip().lower()] = (str(d.get("hsn_code") or "").strip()[:20], str(d.get("gst_percent") or "").strip())
    return out


def hsn_for(info: dict[str, tuple[str, str]], name: str) -> str:
    return info.get((name or "").strip().lower(), ("", ""))[0]


def piece(line) -> str:
    """How one quotation line reads inside a combined charge: the container breakup, or container type / basis with quantity and rate."""
    if getattr(line, "container_breakup", None):
        return ", ".join(f"{b['type']} x{b['qty']} @ {b['rate']}" for b in line.container_breakup)
    label = (getattr(line, "container_type", None) or getattr(line, "basis", "") or "").strip()
    return f"{label + ' ' if label else ''}x{line.qty.normalize():f} @ {line.rate.normalize():f}"


def parts_of(line) -> list[dict]:
    """The container-wise parts of one quotation line: its break-up rows, else the line itself (container type, quantity, rate)."""
    if getattr(line, "container_breakup", None):
        return [{"type": str(b["type"]), "qty": Decimal(str(b["qty"])), "rate": Decimal(str(b["rate"]))} for b in line.container_breakup if Decimal(str(b["qty"])) > 0]
    return [{"type": (getattr(line, "container_type", None) or "").strip(), "qty": line.qty, "rate": line.rate}]


def merge_lines(lines: list) -> list[tuple[object, str, list | None]]:
    """Group quotation lines of the same charge, currency, rate of exchange and tax into ONE head. Returns (line, detail, parts). A single plain line comes back as it is (no detail, no parts). A group - or a line
    that carries a container break-up - becomes one line for the whole quantity (qty 1, rate = the total in its own currency); `parts` lists the container-wise rows so the job charge can keep the break-up and
    the invoice can show it in its description."""
    groups: dict[tuple, list] = {}
    for l in lines:
        groups.setdefault(((l.charge_name or "").strip().lower(), l.ccy, str(l.exch), bool(l.taxable), str(l.tax_pct)), []).append(l)
    out: list[tuple[object, str, list | None]] = []
    for ls in groups.values():
        if len(ls) == 1 and not getattr(ls[0], "container_breakup", None):
            out.append((ls[0], "", None))
            continue
        first = ls[0]
        parts = [p for l in ls for p in parts_of(l)]
        total = sum((p["qty"] * p["rate"] for p in parts), Decimal(0))
        merged = SimpleNamespace(charge_name=first.charge_name, ccy=first.ccy, exch=first.exch, taxable=first.taxable, tax_pct=first.tax_pct, basis=first.basis if len(ls) == 1 else "Per Container", qty=Decimal(1), rate=total,
                                 amount=sum((l.amount for l in ls), Decimal(0)), amount_fcy=sum((l.amount_fcy or 0 for l in ls), Decimal(0)), container_breakup=None, container_type=None)
        out.append((merged, "; ".join(f"{p['type'] + ' ' if p['type'] else ''}x{p['qty'].normalize():f} @ {p['rate'].normalize():f}" for p in parts), parts))
    return out


def add_side(breakup: list[dict] | None, side: str, parts: list[dict]) -> list[dict]:
    """Merge one side's container-wise rates into a job charge's break-up: rows of the same container type share a line (sell and buy rate side by side)."""
    rows = [dict(r) for r in (breakup or [])]
    other = "buy" if side == "sell" else "sell"
    for p in parts:
        hit = next((r for r in rows if r["type"].strip().lower() == p["type"].strip().lower() and Decimal(str(r.get(side) or 0)) == 0), None)
        if hit is None:
            rows.append({"type": p["type"], "qty": str(p["qty"]), side: str(p["rate"]), other: "0"})
        else:
            q = Decimal(str(hit["qty"]))
            hit[side] = str(p["rate"] if q == p["qty"] or q == 0 else (p["qty"] * p["rate"] / q).quantize(Decimal("0.0001")))
    return rows


def breakup_text(breakup, side: str) -> str:
    """The container-wise break-up of a job charge as it reads on a sales (side='sell') or purchase (side='buy') document: 20GP x2 @ 2500; 40HC x1 @ 3200."""
    parts = []
    for b in breakup or []:
        rate = Decimal(str(b.get(side) or 0))
        if rate <= 0:
            continue
        qty = Decimal(str(b.get("qty") or 0))
        parts.append(f"{(b.get('type') or '').strip()} x{qty.normalize():f} @ {rate.normalize():f}".strip())
    return "; ".join(parts)


def side_description(row, sell: bool) -> str:
    """Description of a job charge on a document: its own remarks plus the container-wise break-up for that side."""
    base = (row.description or "").strip()
    pct = getattr(row, "sell_pct" if sell else "buy_pct", None)
    if pct is not None and row.uom in ("% of Freight", "% of Other Charges"):         # a percentage charge says what it is a percentage of
        what = f"{Decimal(pct).normalize():f}% of {'freight' if row.uom == '% of Freight' else 'other charges'}"
        return (f"{base} ({what})" if base else f"{row.charge_name} — {what}")[:240]
    text = breakup_text(row.breakup, "sell" if sell else "buy")
    if not text:
        return base
    return (f"{base} ({text})" if base else f"{row.charge_name} — {text}")[:240]
