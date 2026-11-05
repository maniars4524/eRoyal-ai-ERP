"""The cargo engine: the prompt's own test cases, units, rounding, validation, no double counting, and proof that the existing calculators give exactly the same results as before."""
import random
from decimal import Decimal as D

import pytest

from app import air_weight, basis_calc as bc, cargo_engine as ce
from tests.golden import air_weight_legacy as old_air, basis_calc_legacy as old_bc
from tests.test_bookings import client, hdr


def air(rows, gross=None, **kw):
    return ce.compute(rows, "Air", "", shipment_gross_kg=gross, **kw)


def lcl(rows, gross=None, **kw):
    return ce.compute(rows, "Sea", "LCL", shipment_gross_kg=gross, **kw)


# ---------------------------------------------------------------- the prompt's tests
def test_1_air_gross_weight_higher():
    r = air([{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 200}])
    assert (r["cbm"], r["volumetric_kg"], r["chargeable_kg"], r["basis"]) == ("0.960", "160.000", "200.000", "actual")


def test_2_air_volumetric_weight_higher():
    r = air([{"packages": 2, "length": 100, "width": 80, "height": 60}], gross=100)
    assert (r["volumetric_kg"], r["chargeable_kg"], r["basis"]) == ("160.000", "160.000", "volumetric")


def test_3_multiple_rows():
    r = air([{"packages": 2, "length": 100, "width": 80, "height": 60}, {"packages": 3, "length": 120, "width": 100, "height": 80}], gross=100)
    assert [x["cbm"] for x in r["rows"]] == ["0.960", "2.880"] and r["cbm"] == "3.840"
    assert [x["volumetric_kg"] for x in r["rows"]] == ["160.000", "480.000"] and r["volumetric_kg"] == "640.000" and r["chargeable_kg"] == "640.000" and r["packages"] == "5"


def test_4_lcl_volume_higher():
    r = lcl([{"packages": 1, "length": 100, "width": 100, "height": 500}], gross=3000)
    assert (r["cbm"], r["weight_mt"], r["chargeable_rt"], r["basis"]) == ("5.000", "3.000", "5.000", "volume")


def test_5_lcl_weight_higher():
    r = lcl([{"packages": 1, "length": 100, "width": 100, "height": 200}], gross=4000)
    assert (r["cbm"], r["weight_mt"], r["chargeable_rt"], r["basis"]) == ("2.000", "4.000", "4.000", "weight")


# ---------------------------------------------------------------- units, weights, rounding, configuration
def test_every_length_unit_gives_the_same_volume():
    one = lambda unit, v: ce.compute([{"packages": 1, "length": v[0], "width": v[1], "height": v[2], "unit": unit}], "Sea", "LCL")["cbm"]
    assert one("CM", (100, 80, 60)) == one("MM", (1000, 800, 600)) == one("M", (1, "0.8", "0.6")) == "0.480"
    assert one("INCH", (100, 100, 100)) == "16.387" and one("FEET", (3, 3, 3)) == "0.765"


def test_weight_units_and_the_meaning_of_the_row_weight():
    assert air([{"packages": 1, "gross": 1, "gross_unit": "TON"}])["gross_kg"] == "1000.000"
    total = air([{"packages": 4, "length": 10, "width": 10, "height": 10, "gross": 50}])                 # a row weight is the weight of the whole row
    per_pkg = air([{"packages": 4, "length": 10, "width": 10, "height": 10, "gross": 50, "weight_basis": "PER_PACKAGE"}])   # older jobs: weight of one package
    assert total["gross_kg"] == "50.000" and per_pkg["gross_kg"] == "200.000"
    mixed = air([{"packages": 1, "gross": 30}, {"packages": 1, "gross": 20}], gross=999)
    assert mixed["gross_kg"] == "50.000"                                                                  # row weights replace the shipment weight, never added to it


def test_rounding_rules_and_minimums():
    base = [{"packages": 1, "length": 100, "width": 100, "height": 100}]                                  # 1,000,000 / 6000 = 166.667 kg
    chg = lambda rule: ce.compute(base, "Air", cfg=ce.Config(rounding=rule))["chargeable_kg"]
    assert chg("UP_HALF") == "167.000" and chg("UP_ONE") == "167.000" and chg("NONE") == "166.667" and chg("NEAREST_HALF") == "166.500"
    assert ce.round_chargeable_kg(D("100.01"), "UP_HALF") == D("100.5") and ce.round_chargeable_kg(D("99.5"), "UP_HALF") == D("99.5")
    assert ce.compute(base, "Air", cfg=ce.Config(min_chargeable_kg=D(300)))["chargeable_kg"] == "300.000"
    assert ce.compute(base, "Air", cfg=ce.Config(divisor=D(5000), rounding="NONE"))["chargeable_kg"] == "200.000"            # express divisor
    lc = ce.compute([{"packages": 1, "length": 100, "width": 100, "height": 100}], "Sea", "LCL", cfg=ce.Config(wm_kg_per_cbm=D(500), rt_rounding="UP_HALF"), shipment_gross_kg=620)
    assert lc["chargeable_rt"] == "1.500"                                                                 # 620 kg / 500 = 1.24 RT against 1 CBM -> 1.24, up to 1.5
    assert ce.compute([{"packages": 1, "length": 10, "width": 10, "height": 10}], "Sea", "LCL", cfg=ce.Config(min_rt=D(1)))["chargeable_rt"] == "1.000"


def test_nothing_is_rounded_per_row():
    rows = [{"packages": 1, "length": 10, "width": 10, "height": 4}] * 3                                  # each row is 0.0004 CBM
    r = lcl(rows)
    assert all(x["cbm"] == "0.000" for x in r["rows"]) and r["cbm"] == "0.001"                              # shown rounded, summed exactly: 0.0012


def test_fcl_keeps_container_charging_and_missing_data_is_a_warning_not_zero():
    f = ce.compute([{"packages": 5, "length": 100, "width": 100, "height": 100, "gross": 2000}], "Sea", "FCL")
    assert f["chargeable_kg"] is None and f["chargeable_rt"] is None and f["cbm"] == "5.000" and "container" in f["warnings"][0]
    e = air([])
    assert e["chargeable_kg"] is None and e["warnings"]
    assert lcl([], gross=0)["chargeable_rt"] is None
    assert lcl([], gross=2500)["chargeable_rt"] == "2.500" and lcl([], gross=2500)["warnings"]


def test_validation_messages():
    bad = lambda rows, **kw: pytest.raises(ce.CargoError, ce.compute, rows, "Air", **kw)
    assert "length, width and height" in str(bad([{"length": 10, "width": 0, "height": 5, "packages": 1}]).value)
    assert "unit" in str(bad([{"length": 1, "width": 1, "height": 1, "unit": "YARD"}]).value)
    assert "weight unit" in str(bad([{"gross": 1, "gross_unit": "OZ"}]).value)
    assert "negative" in str(bad([{"length": -1, "width": 1, "height": 1}]).value)
    assert "not a number" in str(bad([{"length": "abc", "width": 1, "height": 1}]).value)
    assert "packages" in str(bad([{"length": 1, "width": 1, "height": 1, "packages": 0}]).value)
    assert "greater than zero" in str(pytest.raises(ce.CargoError, ce.compute, [], "Air", cfg=ce.Config(divisor=D(0))).value)
    assert air([{"packages": "", "length": "", "width": "", "height": "", "gross": ""}])["rows"] == []              # an empty row is ignored


# ---------------------------------------------------------------- the existing calculators give exactly what they gave before
def test_job_air_weight_is_unchanged():
    rnd = random.Random(7)
    for _ in range(400):
        rows = [{"pkg": str(rnd.randint(1, 20)), "l": str(rnd.randint(5, 250) + rnd.choice([0, .5, .25])), "w": str(rnd.randint(5, 200)), "h": str(rnd.randint(5, 180)), "awt": str(rnd.choice([0, 12.5, 40, 100.25, 500]))} for _ in range(rnd.randint(1, 4))]
        a, b = old_air.compute({"unit": "cm_kg", "rows": rows}), air_weight.compute({"unit": "cm_kg", "rows": rows})
        assert a["chargeable_kg"] == b["chargeable_kg"] and a["basis"] == b["basis"] and a["packages"] == b["packages"]
        assert abs(a["actual_kg"] - b["actual_kg"]) < 0.0011 and abs(a["volumetric_kg"] - b["volumetric_kg"]) < 0.0011
    for unit in ("in_lbs", "cbm"):
        rows = [{"pkg": "2", "l": "40", "w": "20", "h": "20", "awt": "30"}]
        assert old_air.compute({"unit": unit, "rows": rows}) == air_weight.compute({"unit": unit, "rows": rows})
    assert old_air.round_up_half(100.01) == air_weight.round_up_half(100.01) == 100.5


def test_basis_calculation_is_unchanged():
    rnd = random.Random(11)
    for _ in range(300):
        dims = [{"l": rnd.randint(5, 250), "w": rnd.randint(5, 200), "h": rnd.randint(5, 180), "packages": rnd.randint(1, 15)} for _ in range(rnd.randint(1, 4))]
        kw = {"mode": rnd.choice(["Air", "Sea", "Road"]), "shipment_type": rnd.choice(["LCL", "FCL", ""]), "gross_kg": rnd.choice([0, 10, 125.5, 800, 4000]), "packages": rnd.randint(1, 30), "dims": dims,
              "divisor": rnd.choice([None, 5000, 6000])}
        new, old = bc.Measures(**kw), old_bc.Measures(**kw)
        assert new.volumetric_kg == old.volumetric_kg and new.dims_cbm == old.dims_cbm and new.chargeable_kg == old.chargeable_kg
        for basis in bc.BASES:
            if basis in bc.PERCENT_BASES:
                continue
            try:
                a = old_bc.quantity(basis, old)
            except old_bc.BasisError as e:
                with pytest.raises(bc.BasisError, match=str(e)[:25]):
                    bc.quantity(basis, new)
                continue
            assert bc.quantity(basis, new) == a


# ---------------------------------------------------------------- the service for screens
def test_calc_service_matches_the_engine_and_reports_errors(world, admin):
    h = hdr(admin["username"], admin["license"])
    body = {"mode": "Air", "rows": [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 100}]}
    r = client.post("/api/cargo/calc", json=body, headers=h)
    assert r.status_code == 200 and r.json()["volumetric_kg"] == "160.000" and r.json()["chargeable_kg"] == "160.000" and r.json()["method"]["divisor"] == "6000"
    assert client.post("/api/cargo/calc", json={**body, "express": True}, headers=h).json()["volumetric_kg"] == "192.000"          # express divisor 5000
    bad = client.post("/api/cargo/calc", json={"mode": "Air", "rows": [{"length": 10, "width": 0, "height": 5}]}, headers=h)
    assert bad.status_code == 422 and "length, width and height" in bad.json()["detail"]
    assert client.post("/api/cargo/calc", json=body).status_code == 401
    assert client.get("/api/cargo/config", headers=h).json()["length_units"] == ["CM", "MM", "M", "INCH", "FEET"]
