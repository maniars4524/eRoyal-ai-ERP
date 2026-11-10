"""Job dimensions in the new format: Air and Sea LCL totals from the engine, saved with their calculation method, charge quantities that follow, old jobs untouched."""
import pytest

from tests.test_bookings import client, hdr
from tests.test_job_details import job


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def lcl_job(h):
    r = client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "container_mode": "LCL", "pol": "INMAA(CHENNAI)", "pod": "SGSIN(SINGAPORE)"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["job_number"]


def put_dims(h, j, rows, **extra):
    return client.put(f"/api/jobs/{j}/details/dims", json={"data": {"version": 2, "rows": rows, **extra}}, headers=h)


def side(rate):
    return {"ccy": "INR", "exch": "1", "rate": rate, "tax_type": "Non-Taxable", "tax_pct": "0", "party": "", "use_party": False}


def test_air_job_saves_the_new_format_and_returns_authoritative_totals(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    r = put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "unit": "CM", "gross": 100}])
    assert r.status_code == 200, r.text
    d = r.json()["dims"]
    t = d["totals"]
    assert t["volumetric_kg"] == 160 and t["actual_kg"] == 100 and t["chargeable_kg"] == 160 and t["basis"] == "volumetric" and t["cbm"] == "0.960" and t["packages"] == 2
    assert d["calculation"]["method"]["divisor"] == "6000" and d["calculation"]["method"]["configuration"] and d["calculation"]["chargeable_kg"] == "160.000"       # kept for audit
    assert r.json()["container_mode"] is None


def test_mixed_units_and_validation_on_save(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    ok = put_dims(h, j, [{"packages": 1, "length": 1000, "width": 800, "height": 600, "unit": "MM", "gross": 0.2, "gross_unit": "TON"}])
    assert ok.json()["dims"]["totals"]["cbm"] == "0.480" and ok.json()["dims"]["totals"]["actual_kg"] == 200
    for rows, msg in (([{"packages": 1, "length": 10, "width": 0, "height": 5}], "length, width and height"), ([{"packages": 1, "length": 1, "width": 1, "height": 1, "unit": "YARD"}], "unit"),
                      ([{"packages": 0, "length": 1, "width": 1, "height": 1}], "packages"), ([{"packages": 1, "length": -4, "width": 1, "height": 1}], "negative")):
        r = put_dims(h, j, rows)
        assert r.status_code == 422 and msg in r.json()["detail"], r.text
    assert client.get(f"/api/jobs/{j}/details", headers=h).json()["dims"]["totals"]["cbm"] == "0.480"                          # a refused save changes nothing


def test_air_charge_quantity_follows_the_chargeable_weight_without_duplicating_the_charge(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 100}])
    line = {"charge_name": "Air Freight", "qty": "1", "uom": "By Chg Wt", "sell": side("150"), "buy": side("120")}
    put = client.put(f"/api/shipments/by-number/{j}/charges", json={"lines": [line, {"charge_name": "Handling", "qty": "1", "uom": "Per Shipment", "sell": side("500"), "buy": side("400")}]}, headers=h)
    assert put.status_code == 200, put.text
    assert float(put.json()["lines"][0]["qty"]) == 160 and float(put.json()["lines"][1]["qty"]) == 1                       # air freight on the chargeable KG, handling untouched
    put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 300}])                              # heavier than the volume now
    again = client.get(f"/api/shipments/by-number/{j}/charges", headers=h).json()["lines"]
    assert len(again) == 2 and float(again[0]["qty"]) == 300 and float(again[1]["qty"]) == 1


def test_lcl_job_gets_cbm_and_revenue_ton_and_the_wm_charge_follows(h):
    j = lcl_job(h)
    r = put_dims(h, j, [{"packages": 1, "length": 100, "width": 100, "height": 500}], shipment_gross_kg=3000)
    t = r.json()["dims"]["totals"]
    assert r.json()["container_mode"] == "LCL" and t["cbm"] == "5.000" and t["weight_mt"] == "3.000" and t["chargeable_rt"] == "5.000" and t["basis"] == "volume"
    line = {"charge_name": "Ocean Freight", "qty": "1", "uom": "By Gross Wt/Volume", "sell": side("50"), "buy": side("40")}
    put = client.put(f"/api/shipments/by-number/{j}/charges", json={"lines": [line]}, headers=h)
    assert put.status_code == 200, put.text
    assert float(put.json()["lines"][0]["qty"]) == 5
    put_dims(h, j, [{"packages": 1, "length": 100, "width": 100, "height": 200}], shipment_gross_kg=4000)                    # 2 CBM, 4 MT -> 4 RT
    again = client.get(f"/api/shipments/by-number/{j}/charges", headers=h).json()["lines"]
    assert len(again) == 1 and float(again[0]["qty"]) == 4
    assert client.get(f"/api/jobs/{j}/details", headers=h).json()["dims"]["totals"]["chargeable_rt"] == "4.000"


def test_fcl_job_records_the_figures_but_gets_no_chargeable_quantity(h):
    j = job(h, mode="Sea", pol="INMAA(CHENNAI)", pod="SGSIN(SINGAPORE)")
    t = put_dims(h, j, [{"packages": 5, "length": 100, "width": 100, "height": 100, "gross": 2000}]).json()["dims"]["totals"]
    assert t["cbm"] == "5.000" and t["chargeable_rt"] is None and t["chargeable_kg"] == 0 and "container" in t["warnings"][0]


def test_older_jobs_and_the_old_save_format_are_untouched(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    r = client.put(f"/api/jobs/{j}/details/dims", json={"data": {"unit": "cm_kg", "rows": [{"desc": "Cartons", "pkg": "2", "l": "100", "w": "60", "h": "50", "awt": "40"}]}}, headers=h)
    t = r.json()["dims"]["totals"]
    assert t["actual_kg"] == 80 and t["volumetric_kg"] == 100 and t["chargeable_kg"] == 100 and "calculation" not in r.json()["dims"]
    assert "version" not in r.json()["dims"]


# ---------------------------------------------------------------- quotation -> job
def quote_body(**kw):
    return {"party_name": "Dims Ltd", "mode": "Air", "direction": "Export", "shipment_type": "General", "pol": "MAA", "pod": "FRA", "lines": [], **kw}


ROWS = [{"packages": "2", "package_type": "BOX", "length": "100", "width": "80", "height": "60", "unit": "CM", "gross": "100", "gross_unit": "KG", "weight_basis": "TOTAL", "reference": "", "remarks": ""}]


def test_quotation_dimensions_update_the_headline_figures_on_the_server(h):
    q = client.post("/api/quotations", json=quote_body(header={"dim_rows": ROWS, "packages": "99", "chargeable_weight": "1"}), headers=h)
    assert q.status_code == 201, q.text
    hd = q.json()["header"]
    assert hd["packages"] == "2" and hd["gross_weight"] == "100.000" and hd["volume"] == "0.960" and hd["chargeable_weight"] == "160.000"        # typed numbers are replaced by the calculated ones
    assert "divisor 6000" in hd["dim_calculation"]
    bad = client.post("/api/quotations", json=quote_body(header={"dim_rows": [{"packages": "1", "length": "10", "width": "0", "height": "5"}]}), headers=h)
    assert bad.status_code == 422 and "Dimensions:" in bad.json()["detail"]
    up = client.patch(f"/api/quotations/{q.json()['id']}", json={"header": {**hd, "dim_rows": [{**ROWS[0], "packages": "4"}]}}, headers=h)
    assert up.status_code == 200 and up.json()["header"]["packages"] == "4" and up.json()["header"]["chargeable_weight"] == "320.000"
    cleared = client.patch(f"/api/quotations/{q.json()['id']}", json={"header": {k: v for k, v in up.json()["header"].items() if not k.startswith("dim_")}}, headers=h)
    assert cleared.status_code == 200 and "dim_rows" not in cleared.json()["header"]


def test_quotation_dimensions_reach_the_job_once_and_keep_the_quoted_rows(h):
    from tests.test_quotations import line

    q = client.post("/api/quotations", json=quote_body(header={"dim_rows": ROWS}, lines=[line(basis="Per Shipment", rate="100", qty="1")]), headers=h).json()
    for step in ("submit", "approve", "send", "accept"):
        path = {"submit": "approval/submit", "approve": "approval/approve", "send": "send", "accept": "accept"}[step]
        r = client.post(f"/api/quotations/{q['id']}/{path}", headers=h)
        assert r.status_code in (200, 201), (step, r.text)
    conv = client.post(f"/api/quotations/{q['id']}/convert", headers=h)
    assert conv.status_code == 200, conv.text
    jn = conv.json()["job_number"]
    d = client.get(f"/api/jobs/{jn}/details", headers=h).json()
    assert d["dims"]["version"] == 2 and len(d["dims"]["rows"]) == 1 and d["dims"]["source"]["quotation"] == q["number"] and d["dims"]["totals"]["chargeable_kg"] == 160
    assert d["dims"]["rows"][0]["length"] == "100" and d["dims"]["calculation"]["method"]["divisor"] == "6000"
    again = client.get(f"/api/quotations/{q['id']}", headers=h).json()
    assert again["header"]["dim_rows"][0]["length"] == "100"                                                              # the quotation keeps what was quoted
    changed = client.put(f"/api/jobs/{jn}/details/dims", json={"data": {"version": 2, "rows": [{**ROWS[0], "packages": "3"}]}}, headers=h)
    assert changed.status_code == 200 and changed.json()["dims"]["totals"]["packages"] == 3
    assert client.get(f"/api/quotations/{q['id']}", headers=h).json()["header"]["dim_rows"][0]["packages"] == "2"          # later job edits never rewrite the quotation


def test_consolidation_cargo_is_listed_by_house_reference_without_double_counting(h):
    from app import cargo_engine as ce

    rows = [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 100, "reference": "HAWB-1"}, {"packages": 3, "length": 120, "width": 100, "height": 80, "gross": 200, "reference": "HAWB-2"},
            {"packages": 1, "length": 50, "width": 50, "height": 50, "gross": 10, "reference": "HAWB-1"}]
    r = ce.compute(rows, "Air")
    refs = {x["reference"]: x for x in r["by_reference"]}
    assert refs["HAWB-1"]["packages"] == "3" and refs["HAWB-2"]["packages"] == "3" and r["packages"] == "6" and r["gross_kg"] == "310.000"
    assert sum(float(x["gross_kg"]) for x in r["by_reference"]) == float(r["gross_kg"])                                    # the master total is the sum of the houses, nothing counted twice


def test_saved_dimensions_fill_the_cargo_figures_and_the_lcl_zero_container_line(h):
    j = lcl_job(h)
    r = put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 150}, {"packages": 3, "length": 50, "width": 50, "height": 50, "gross": 90}])
    assert r.status_code == 200, r.text
    d = client.get(f"/api/jobs/{j}/details", headers=h).json()
    assert d["cargo"]["pkgs"] == "5" and d["cargo"]["gwt"] == "240" and d["cargo"]["vol"] == "1.335" and d["cargo"]["chgRt"]
    assert d["containers"] == [{"container_no": "", "type": "LCL", "seal_no": "", "from_dims": True, "pkgs": "5", "gross_wt": "240", "vol": "1.335"}]
    # the container number is typed on that line later; the next dimension save still updates its figures
    cont = dict(d["containers"][0], container_no="MSCU1234567", seal_no="S1")
    assert client.put(f"/api/jobs/{j}/details/containers", json={"data": [cont]}, headers=h).status_code == 200
    put_dims(h, j, [{"packages": 4, "length": 100, "width": 100, "height": 100, "gross": 500}])
    d2 = client.get(f"/api/jobs/{j}/details", headers=h).json()
    assert len(d2["containers"]) == 1 and d2["containers"][0]["container_no"] == "MSCU1234567" and d2["containers"][0]["pkgs"] == "4" and d2["containers"][0]["vol"] == "4" and d2["cargo"]["pkgs"] == "4"


def test_air_dimensions_give_the_cargo_figures_and_the_chargeable_weight(h):
    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 100}])
    c = client.get(f"/api/jobs/{j}/details", headers=h).json()["cargo"]
    assert c["pkgs"] == "2" and c["gwt"] == "100" and c["vol"] == "0.96" and c["chgWt"] == "160" and c["volWt"] == "160"


def test_a_quantity_typed_over_the_calculated_one_is_audited(h):
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import AuditLog

    j = job(h, mode="Air", pol="MAA(CHENNAI)", pod="FRA(FRANKFURT)")
    put_dims(h, j, [{"packages": 2, "length": 100, "width": 80, "height": 60, "gross": 100}])
    line = {"charge_name": "Air Freight", "qty": "200", "qty_manual": True, "uom": "By Chg Wt", "sell": side("150"), "buy": side("120")}
    r = client.put(f"/api/shipments/by-number/{j}/charges", json={"lines": [line]}, headers=h)
    assert r.status_code == 200, r.text
    ev = client.get(f"/api/jobs/{j}/events", headers=h)
    text = ev.text if ev.status_code == 200 else ""
    with SessionLocal() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "JOB_CHARGE_QTY_OVERRIDDEN")))
    assert any(j in str(x.new_value) and "160" in str(x.new_value) and "200" in str(x.new_value) for x in rows)
    n = len(rows)
    client.put(f"/api/shipments/by-number/{j}/charges", json={"lines": [line]}, headers=h)          # saved again unchanged: not logged twice
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "JOB_CHARGE_QTY_OVERRIDDEN")))) == n
