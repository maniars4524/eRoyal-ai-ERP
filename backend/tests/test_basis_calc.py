"""Basis-based calculation: the brief's tests A-E, the safeguards, and the HTTP service."""
from decimal import Decimal

import pytest

from app import basis_calc as bc
from tests.test_bookings import client, hdr


def run(lines, **m):
    return bc.calc_lines(lines, bc.Measures(**m))


def line(basis, rate, side="SELL", **extra):
    return {"id": extra.pop("id", f"{side}-{basis}"), "side": side, "basis": basis, "rate": str(rate), "name": extra.pop("name", "Charge"), **extra}


def by_id(res, i):
    return next(x for x in res["lines"] if x["id"] == i)


def test_a_air_freight_chargeable_weight_is_the_greater_of_gross_and_volumetric():
    # 120 kg gross; 180 kg volumetric = 90 x 60 x 100 cm x 2 packages / 6000 * ... : one row 60 x 90 x 100 x 2 / 6000 = 180
    res = run([line("By Chg Wt", 40, "BUY", id="c", name="Air freight", category="Freight"), line("By Chg Wt", 50, "SELL", id="s", name="Air freight", category="Freight")],
              mode="Air", gross_kg=120, dims=[{"l": 60, "w": 90, "h": 100, "packages": 2}])
    assert res["measures"]["volumetric_kg"] == "180.000" and res["measures"]["chargeable_kg"] == "180.000"
    assert by_id(res, "c")["quantity"] == "180.000" and by_id(res, "c")["amount"] == "7200.00" and by_id(res, "s")["amount"] == "9000.00"
    assert res["totals"]["cost"] == "7200.00" and res["totals"]["sell"] == "9000.00" and res["totals"]["gross_profit"] == "1800.00" and res["totals"]["margin_pct"] == "20.00"


def test_air_divisor_is_configurable():
    m = bc.Measures(mode="Air", gross_kg=10, dims=[{"l": 50, "w": 50, "h": 50, "packages": 1}], divisor=5000)
    assert m.volumetric_kg == Decimal(25) and bc.quantity(bc.BY_CHG, m)[0] == Decimal("25.000")
    assert bc.Measures(mode="Air", gross_kg=10, dims=[{"l": 50, "w": 50, "h": 50, "packages": 1}]).volumetric_kg.quantize(Decimal("0.001")) == Decimal("20.833")


def test_b_sea_lcl_uses_the_greater_of_tonnes_and_cbm():
    res = run([line("By Gross Wt/Volume", 2500, "BUY", id="c"), line("By Gross Wt/Volume", 3500, "SELL", id="s")], mode="Sea", shipment_type="LCL", gross_kg=2000, volume_cbm=3)
    c, s = by_id(res, "c"), by_id(res, "s")
    assert c["quantity"] == "3.000" and c["unit"] == "W/M" and c["amount"] == "7500.00" and s["amount"] == "10500.00"
    assert res["totals"]["gross_profit"] == "3000.00" and res["totals"]["margin_pct"] == "28.57" and res["totals"]["margin_pct_precise"].startswith("28.5714")
    assert res["measures"]["weight_tonnes"] == "2.000"


def test_cbm_from_dimensions_across_packages():
    m = bc.Measures(mode="Sea", shipment_type="LCL", gross_kg=500, dims=[{"l": 100, "w": 100, "h": 100, "packages": 2}, {"l": 50, "w": 40, "h": 30, "packages": 5}])
    assert m.volume_cbm == Decimal("2.3")
    assert bc.quantity(bc.BY_VOLUME, m)[0] == Decimal("2.300")


def test_gross_wt_volume_is_not_mixed_for_other_modes():
    res = run([line("By Gross Wt/Volume", 10, id="x")], mode="Sea", shipment_type="FCL", gross_kg=1000, volume_cbm=5)
    assert by_id(res, "x")["error"] and "Air and to Sea LCL" in by_id(res, "x")["error"]
    air = run([line("By Gross Wt/Volume", 1, id="x")], mode="Air", gross_kg=120, dims=[{"l": 60, "w": 90, "h": 100, "packages": 2}])
    assert by_id(air, "x")["quantity"] == "180.000" and by_id(air, "x")["unit"] == "KG"


def test_c_per_package():
    res = run([line("Per Package", 100, "BUY", id="c"), line("Per Package", 150, "SELL", id="s")], packages=10)
    assert by_id(res, "c")["amount"] == "1000.00" and by_id(res, "s")["amount"] == "1500.00"


def test_e_the_other_bases():
    m = dict(mode="Road", packages=7, gross_kg=1250, net_kg=1100, volume_cbm=4.5, vehicles=3)
    expect = {"Per Shipment": ("1.000", "SHP", "100.00"), "By Gross Wt": ("1250.000", "KG", "125000.00"), "By Net Wt": ("1100.000", "KG", "110000.00"),
              "By Volume": ("4.500", "CBM", "450.00"), "Per Vehicle": ("3.000", "VEH", "300.00"), "By Chg Wt": ("4500.000", "KG", "450000.00")}
    for basis, (qty, unit, amount) in expect.items():
        res = run([line(basis, 100, id="x")], **m)
        x = by_id(res, "x")
        assert (x["quantity"], x["unit"], x["amount"]) == (qty, unit, amount), basis


def test_d_percentage_bases_are_independent_per_side_and_never_circular():
    lines = [line("Per Shipment", 1000, "BUY", id="bf", name="Ocean freight", category="Freight"), line("Per Shipment", 1400, "SELL", id="sf", name="Ocean freight", category="Freight"),
             line("Per Shipment", 200, "BUY", id="bo", name="THC", category="Port"), line("Per Shipment", 300, "SELL", id="so", name="THC", category="Port"),
             line("% of Freight", 10, "BUY", id="bp", name="BAF", category="Freight"), line("% of Freight", 10, "SELL", id="sp", name="BAF", category="Freight"),
             line("% of Other Charges", 5, "SELL", id="sop", name="Handling", category="Other")]
    res = run(lines, mode="Sea", shipment_type="FCL")
    assert by_id(res, "bp")["amount"] == "100.00" and by_id(res, "sp")["amount"] == "140.00"            # cost % on the cost freight, sell % on the sell freight
    assert by_id(res, "sop")["amount"] == "15.00"                                                         # 5% of the sell other charges (THC 300), not of the freight, not of the % lines
    assert by_id(res, "sp")["quantity"] == "14.000" and by_id(res, "sp")["rate"] == "10"                  # stored as base/100 x percentage
    assert res["totals"]["cost"] == "1300.00" and res["totals"]["sell"] == "1855.00"
    alone = run([line("% of Freight", 10, id="p", name="BAF", category="Freight")])
    assert by_id(alone, "p")["error"] and "no freight" in by_id(alone, "p")["error"]
    circ = run([line("% of Other Charges", 10, id="a", name="A", category="Other"), line("% of Other Charges", 10, id="b", name="B", category="Other")])
    assert all(x["error"] for x in circ["lines"])                                                          # percentage lines never feed each other
    cur = run([line("Per Shipment", 100, "SELL", id="f", name="Freight", category="Freight", ccy="USD", exch=80), line("% of Freight", 10, "SELL", id="p", name="BAF", category="Freight", ccy="USD", exch=80)])
    assert by_id(cur, "p")["amount"] == "10.00" and by_id(cur, "p")["amount_inr"] == "800.00"


def test_safeguards_missing_negative_and_zero_margin():
    assert by_id(run([line("Per Package", 10, id="x")], packages=0), "x")["error"]
    assert by_id(run([line("By Gross Wt", 10, id="x")]), "x")["error"]
    with pytest.raises(bc.BasisError):
        bc.Measures(gross_kg=-5)
    with pytest.raises(bc.BasisError):
        bc.Measures(dims=[{"l": 10, "w": 0, "h": 5, "packages": 1}])
    res = run([line("Per Shipment", 0, "SELL", id="s"), line("Per Shipment", 50, "BUY", id="b")])
    assert res["totals"]["margin_pct"] is None and res["totals"]["gross_profit"] == "-50.00"           # no division by zero
    keep = run([{"id": "k", "side": "SELL", "basis": "Container", "rate": "100", "qty": "3"}])           # an existing basis keeps its entered quantity
    assert by_id(keep, "k")["amount"] == "300.00" and not by_id(keep, "k")["error"]


def test_http_service(world, admin):
    h = hdr(admin["username"], admin["license"])
    assert [b["basis"] for b in client.get("/api/basis/bases", headers=h).json()["bases"]] == bc.BASES
    r = client.post("/api/basis/calc", json={"measures": {"mode": "Sea", "shipment_type": "LCL", "gross_kg": 2000, "volume_cbm": 3}, "lines": [{"id": "s", "side": "SELL", "basis": "By Gross Wt/Volume", "rate": 3500}]}, headers=h)
    assert r.status_code == 200 and r.json()["lines"][0]["amount"] == "10500.00"
    assert client.post("/api/basis/calc", json={"measures": {"gross_kg": -1}, "lines": []}, headers=h).status_code == 422
    assert client.post("/api/basis/calc", json={"lines": []}).status_code == 401


# ---------------- job cost: the charges save uses the same service ----------------
def _job(h, mode="Sea", container_mode="LCL", cargo=None):
    made = client.post("/api/shipments", json={"mode": mode, "direction": "Export", "container_mode": container_mode, "pol": "INMAA", "pod": "BEANR"}, headers=h)
    assert made.status_code == 201, made.text
    no = made.json()["job_number"]
    if cargo:
        assert client.put(f"/api/jobs/{no}/details/cargo", json={"data": cargo}, headers=h).status_code == 200
    return no


def _ln(name, uom, buy, sell, qty="1", **extra):
    return {"charge_name": name, "uom": uom, "qty": qty, "buy": {"rate": str(buy), "ccy": "INR", "exch": "1", "tax_type": "Non-Taxable"}, "sell": {"rate": str(sell), "ccy": "INR", "exch": "1", "tax_type": "Non-Taxable"}, **extra}


def test_job_cost_sea_lcl_basis_quantity_comes_from_the_job(world, admin):
    h = hdr(admin["username"], admin["license"])
    no = _job(h, cargo={"pkgs": "10", "gwt": "2000", "nwt": "1900", "vol": "3"})
    r = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [_ln("Ocean Freight", "By Gross Wt/Volume", 2500, 3500), _ln("Handling", "Per Package", 100, 150), _ln("Doc", "Per Shipment", 50, 80)]}, headers=h)
    assert r.status_code == 200, r.text
    by = {l["charge_name"]: l for l in r.json()["lines"]}
    assert by["Ocean Freight"]["qty"] == "3.000" and by["Ocean Freight"]["sell"]["amount"] == "10500.00" and by["Ocean Freight"]["buy"]["amount"] == "7500.00"
    assert by["Handling"]["qty"] == "10.000" and by["Handling"]["sell"]["amount"] == "1500.00" and by["Handling"]["buy"]["amount"] == "1000.00"
    again = client.get(f"/api/shipments/by-number/{no}/charges", headers=h).json()["lines"]
    assert {l["charge_name"]: l["qty"] for l in again}["Ocean Freight"] == "3.000"                    # still right after reopening
    r2 = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": again and [_ln(l["charge_name"], l["uom"], l["buy"]["rate"], l["sell"]["rate"], l["qty"]) for l in again]}, headers=h)
    assert r2.status_code == 200 and len(r2.json()["lines"]) == 3                                        # saving again never duplicates a line


def test_job_cost_percentage_lines_and_manual_override(world, admin):
    h = hdr(admin["username"], admin["license"])
    no = _job(h, container_mode="FCL", cargo={"pkgs": "5", "gwt": "1000"})
    lines = [_ln("Ocean Freight", "Per Shipment", 1000, 1400), _ln("THC", "Per Shipment", 200, 300), _ln("BAF", "% of Freight", 10, 10), _ln("Admin", "% of Other Charges", 5, 5)]
    r = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": lines}, headers=h)
    assert r.status_code == 200, r.text
    by = {l["charge_name"]: l for l in r.json()["lines"]}
    assert by["BAF"]["buy"]["amount"] == "100.00" and by["BAF"]["sell"]["amount"] == "140.00" and by["BAF"]["buy"]["pct"] == "10.000"
    assert by["Admin"]["buy"]["amount"] == "10.00" and by["Admin"]["sell"]["amount"] == "15.00"
    # a typed quantity is kept and flagged
    r = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [_ln("Handling", "Per Package", 10, 20, qty="7", qty_manual=True)]}, headers=h)
    assert r.status_code == 200 and r.json()["lines"][0]["qty"] == "7.000" and r.json()["lines"][0]["qty_manual"] is True
    # a percentage with nothing to take it from is refused with the reason
    bad = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [_ln("BAF", "% of Freight", 10, 10)]}, headers=h)
    assert bad.status_code == 422 and "no freight" in bad.text
    # missing measurements are refused, not silently priced at zero
    empty = _job(h, container_mode="FCL")
    miss = client.put(f"/api/shipments/by-number/{empty}/charges", json={"lines": [_ln("Handling", "Per Package", 10, 20)]}, headers=h)
    assert miss.status_code == 422 and "packages" in miss.text
    unpriced = client.put(f"/api/shipments/by-number/{empty}/charges", json={"lines": [_ln("Handling", "Per Package", 0, 0)]}, headers=h)
    assert unpriced.status_code == 200                                                                   # nothing priced yet: no complaint
    # older uoms behave exactly as before
    old = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [_ln("Freight", "Per Container", 100, 200, qty="2")]}, headers=h)
    assert old.status_code == 200 and old.json()["lines"][0]["qty"] == "2.000" and old.json()["lines"][0]["sell"]["amount"] == "400.00"


def test_job_cost_air_chargeable_weight_from_dimensions(world, admin):
    h = hdr(admin["username"], admin["license"])
    no = _job(h, mode="Air", container_mode=None, cargo={"pkgs": "2", "gwt": "120"})
    assert client.put(f"/api/jobs/{no}/details/dims", json={"data": {"unit": "cm_kg", "rows": [{"pkg": "2", "l": "60", "w": "90", "h": "100", "awt": "60"}]}}, headers=h).status_code == 200
    r = client.put(f"/api/shipments/by-number/{no}/charges", json={"lines": [_ln("Air freight", "By Chg Wt", 40, 50)]}, headers=h)
    assert r.status_code == 200, r.text
    ln = r.json()["lines"][0]
    assert ln["qty"] == "180.000" and ln["sell"]["amount"] == "9000.00" and ln["buy"]["amount"] == "7200.00"      # greater of 120 kg gross and 180 kg volumetric
