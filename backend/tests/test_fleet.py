"""Transport fleet: vehicle register, allocation safety rules, documents, workshop work orders, preventive plans, tyres by wheel position, fuel, alerts."""
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from tests.test_bookings import client, hdr
from tests.test_containers import h, make_user  # noqa: F401


@pytest.fixture(scope="module")
def fx(h, world):
    tag = uuid.uuid4().hex[:5].upper()

    def m(t, code, name, data=None):
        r = client.post(f"/api/masters/{t}", json={"code": f"{code}{tag}", "name": f"{name} {tag}", "data": data or {}}, headers=h)
        assert r.status_code == 201, r.text

    m("inv_vehicle_category", "PM", "Prime mover"); m("inv_vehicle_ownership", "OWN", "Company owned", {"company_asset": "Yes"})
    m("inv_axle_config", "AX6", "6x4 tractor", {"number_of_axles": "3", "total_wheel_positions": "6", "position_labels": "Steer-L\nSteer-R, Drive-LO\nDrive-LI,Drive-RI,Drive-RO"})
    m("inv_axle_config", "NOPOS", "No positions yet", {"number_of_axles": "2", "total_wheel_positions": "4"})
    m("inv_document_type", "INS", "Insurance", {"applies_to": "Vehicle", "has_expiry_date": "Yes", "mandatory": "Yes"}); m("inv_document_type", "FIT", "Fitness certificate", {"applies_to": "Both", "has_expiry_date": "Yes", "mandatory": "No"})
    m("inv_document_type", "CSC", "CSC plate", {"applies_to": "Container", "has_expiry_date": "Yes"})
    m("inv_maintenance_category", "PRV", "Preventive service", {"type": "Preventive"}); m("inv_tyre_brand", "MIC", "Michelin"); m("inv_tyre_category", "DRV", "Drive tyre"); m("inv_warehouse", "WH1", "Main store")
    boss = make_user(world, "fboss", ["fleet.read", "fleet.create", "fleet.approve"])
    viewer = make_user(world, "fview", ["fleet.read"])
    return {"tag": tag, "cat": f"Prime mover {tag}", "own": f"Company owned {tag}", "axle": f"6x4 tractor {tag}", "noaxle": f"No positions yet {tag}", "ins": f"Insurance {tag}", "fit": f"Fitness certificate {tag}",
            "csc": f"CSC plate {tag}", "prv": f"Preventive service {tag}", "brand": f"Michelin {tag}", "tcat": f"Drive tyre {tag}", "wh": f"Main store {tag}", "boss": hdr(boss["username"], boss["license"]), "viewer": hdr(viewer["username"], viewer["license"])}


_n = iter(range(1000, 9999))


def new_vehicle(h, fx, **kw):
    body = {"registration_number": f"TN 09 AB {next(_n)}", "category": fx["cat"], "make": "Tata", "ownership": fx["own"], "axle_config": fx["axle"], "odometer": 1000, "chassis_number": f"CH{uuid.uuid4().hex[:10].upper()}", **kw}
    r = client.post("/api/vehicles", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def doc(h, v, dtype, days=365, **kw):
    r = client.post(f"/api/vehicles/{v['id']}/documents", json={"doc_type": dtype, "number": "D1", "issue_date": "2025-01-01", "expiry_date": (date.today() + timedelta(days=days)).isoformat(), **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def ready_vehicle(h, fx, **kw):
    v = new_vehicle(h, fx, **kw)
    doc(h, v, fx["ins"])
    return v


def get(h, v):
    return client.get(f"/api/vehicles/{v['id']}", headers=h).json()


def test_register_normalises_the_number_and_rejects_duplicates_and_unknown_masters(h, fx):
    v = new_vehicle(h, fx, registration_number="tn-09 zz 4321")
    assert v["registration_number"] == "TN09ZZ4321" and v["asset_id"] and v["status"] == "AVAILABLE"
    assert client.post("/api/vehicles", json={"registration_number": "TN 09 ZZ 4321"}, headers=h).status_code == 409
    assert client.post("/api/vehicles", json={"registration_number": "TN09YY1111", "chassis_number": v["chassis_number"].lower()}, headers=h).status_code == 409
    r = client.post("/api/vehicles", json={"registration_number": "TN09YY2222", "category": "Space shuttle"}, headers=h)
    assert r.status_code == 422 and "Masters > Inventory" in r.json()["detail"]
    assert client.post("/api/vehicles", json={"registration_number": "TN09!!"}, headers=h).status_code == 422


def test_a_vehicle_cannot_be_allocated_with_missing_or_expired_mandatory_documents(h, fx):
    v = new_vehicle(h, fx)
    chk = client.get(f"/api/vehicles/{v['id']}/allocation-check", headers=h).json()
    assert chk["allowed"] is False and any("No Insurance" in x for x in chk["blocked_because"])
    assert client.post(f"/api/vehicles/{v['id']}/allocate", json={"driver": "Ravi"}, headers=h).status_code == 409
    doc(h, v, fx["ins"], days=-3)
    r = client.post(f"/api/vehicles/{v['id']}/allocate", json={"driver": "Ravi"}, headers=h)
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    doc(h, v, fx["ins"], days=200)                                                        # renewal: the new document supersedes the expired one
    docs = get(h, v)["documents"]
    assert [d["status"] for d in docs] == ["CURRENT", "SUPERSEDED"]
    r = client.post(f"/api/vehicles/{v['id']}/allocate", json={"driver": "Ravi", "odometer": 1200}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ON_TRIP" and r.json()["odometer"] == 1200
    assert client.post(f"/api/vehicles/{v['id']}/allocate", json={}, headers=h).status_code == 409
    assert client.post(f"/api/vehicles/{v['id']}/release", json={"odometer": 1100}, headers=h).status_code == 409        # odometer cannot go back
    assert client.post(f"/api/vehicles/{v['id']}/release", json={"odometer": 1500}, headers=h).json()["status"] == "AVAILABLE"
    assert {x["field"] for x in get(h, v)["history"]} >= {"status", "odometer", "driver"}


def test_document_rules(h, fx):
    v = new_vehicle(h, fx)
    assert client.post(f"/api/vehicles/{v['id']}/documents", json={"doc_type": fx["csc"], "expiry_date": "2027-01-01"}, headers=h).status_code == 422       # a container document
    assert client.post(f"/api/vehicles/{v['id']}/documents", json={"doc_type": fx["ins"]}, headers=h).status_code == 422                                  # needs an expiry date
    assert client.post(f"/api/vehicles/{v['id']}/documents", json={"doc_type": "Mystery", "expiry_date": "2027-01-01"}, headers=h).status_code == 422
    assert client.post(f"/api/vehicles/{v['id']}/documents", json={"doc_type": fx["ins"], "issue_date": "2026-05-01", "expiry_date": "2026-01-01"}, headers=h).status_code == 422


def test_status_rules_and_disposal(h, fx):
    v = ready_vehicle(h, fx)
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "UNDER_MAINTENANCE", "reason": "manual"}, headers=h).status_code == 422
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "BREAKDOWN", "reason": "engine failure on highway"}, headers=h).json()["status"] == "BREAKDOWN"
    assert client.post(f"/api/vehicles/{v['id']}/allocate", json={}, headers=h).status_code == 409                                                       # not while broken down
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "AVAILABLE", "reason": "repaired"}, headers=h).status_code == 200
    t = client.post("/api/tyres", json={"serial_number": f"SN-{uuid.uuid4().hex[:6]}"}, headers=h).json()
    client.post(f"/api/tyres/{t['id']}/fit", json={"vehicle_id": v["id"], "position": "Steer-L", "odometer": 1100}, headers=h)
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "SOLD", "reason": "sold to Acme"}, headers=h).status_code == 409                  # tyres still fitted
    client.post(f"/api/tyres/{t['id']}/remove", json={"odometer": 1200, "to_status": "IN_STOCK", "reason": "vehicle sold"}, headers=h)
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "SOLD", "reason": "sold to Acme"}, headers=fx["viewer"]).status_code == 403
    assert client.post(f"/api/vehicles/{v['id']}/status", json={"status": "SOLD", "reason": "sold to Acme"}, headers=h).json()["status"] == "SOLD"
    assert client.patch(f"/api/vehicles/{v['id']}", json={"driver": "X"}, headers=h).status_code == 409
    assert client.post(f"/api/vehicles/{v['id']}/allocate", json={}, headers=h).status_code == 409
    assert v["registration_number"] not in {x["registration_number"] for x in client.get("/api/vehicles", headers=h).json()["items"]}                       # sold vehicles leave the active list


def test_workshop_flow_takes_the_vehicle_out_of_service_and_counts_cost_once(h, fx):
    v = ready_vehicle(h, fx)
    assert client.post("/api/vehicle-work-orders", json={"vehicle_id": v["id"]}, headers=h).status_code == 422                                          # nothing described
    w = client.post("/api/vehicle-work-orders", json={"vehicle_id": v["id"], "category": fx["prv"], "complaint": "oil change", "odometer": 1300, "workshop": "Main"}, headers=h).json()
    assert w["status"] == "OPEN" and w["number"]
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/complete", json={"completion_date": "2026-11-05"}, headers=h).status_code == 409                # must start first
    r = client.post(f"/api/vehicle-work-orders/{w['id']}/start", params={"start_date": "2026-11-01"}, headers=h)
    assert r.status_code == 200 and get(h, v)["status"] == "UNDER_MAINTENANCE" and get(h, v)["odometer"] == 1300
    assert client.post(f"/api/vehicles/{v['id']}/allocate", json={}, headers=h).status_code == 409
    c = client.post(f"/api/vehicle-work-orders/{w['id']}/complete", json={"completion_date": "2026-11-03", "labour_cost": "500", "material_cost": "1500", "external_cost": "250", "other_cost": "50",
                                                                          "repair_description": "oil + filter", "next_service_odometer": 11300}, headers=h)
    assert c.status_code == 200 and D(c.json()["total_cost"]) == 2300 and c.json()["downtime_days"] == 3
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/complete", json={"completion_date": "2026-11-03", "labour_cost": "500"}, headers=h).status_code == 409     # costs are not counted twice
    assert get(h, v)["status"] == "UNDER_MAINTENANCE"                                                                                               # still in the workshop until approved
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/close", headers=fx["viewer"]).status_code == 403
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/close", headers=h).status_code == 403                                                  # the creator, while another approver exists
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/close", headers=fx["boss"]).json()["status"] == "CLOSED"
    assert get(h, v)["status"] == "AVAILABLE" and D(get(h, v)["costs"]["maintenance"]) == 2300


def test_cancel_and_trip_conflicts(h, fx):
    v = ready_vehicle(h, fx)
    client.post(f"/api/vehicles/{v['id']}/allocate", json={}, headers=h)
    w = client.post("/api/vehicle-work-orders", json={"vehicle_id": v["id"], "complaint": "noise"}, headers=h).json()
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/start", headers=h).status_code == 409                                                     # on a trip
    client.post(f"/api/vehicles/{v['id']}/release", json={"odometer": 1100}, headers=h)
    client.post(f"/api/vehicle-work-orders/{w['id']}/start", headers=h)
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/cancel", json={"reason": "not needed"}, headers=fx["viewer"]).status_code == 403
    assert client.post(f"/api/vehicle-work-orders/{w['id']}/cancel", json={"reason": "not needed"}, headers=fx["boss"]).json()["status"] == "CANCELLED"
    assert get(h, v)["status"] == "AVAILABLE"


def test_preventive_plan_generates_one_work_order_per_due_interval(h, fx):
    v = ready_vehicle(h, fx, odometer=50000)
    assert client.post("/api/vehicle-plans", json={"vehicle_id": v["id"], "name": "Service"}, headers=h).status_code == 422
    p = client.post("/api/vehicle-plans", json={"vehicle_id": v["id"], "name": "10,000 km service", "category": fx["prv"], "interval_km": 10000}, headers=h).json()
    assert p["last_done_odometer"] == 50000 and p["overdue"] is False and p["km_left"] == 10000
    assert client.post(f"/api/vehicle-plans/{p['id']}/generate", headers=h).status_code == 409                                                         # not due
    client.post(f"/api/vehicles/{v['id']}/odometer", json={"odometer": 59800}, headers=h)
    due = client.get("/api/vehicle-plans", params={"due": True, "vehicle_id": v["id"]}, headers=h).json()["items"][0]
    assert due["due_soon"] is True and due["km_left"] == 200
    w = client.post(f"/api/vehicle-plans/{p['id']}/generate", headers=h)
    assert w.status_code == 201 and w.json()["plan_id"] == p["id"]
    assert client.post(f"/api/vehicle-plans/{p['id']}/generate", headers=h).status_code == 409                                                         # no duplicate for the same interval
    made = client.post("/api/vehicle-plans/generate-due", headers=h).json()
    assert all(x["registration_number"] != v["registration_number"] for x in made["created"]) and made["skipped_already_open"] >= 1       # this plan already has an open order
    client.post(f"/api/vehicle-work-orders/{w.json()['id']}/start", headers=h)
    client.post(f"/api/vehicle-work-orders/{w.json()['id']}/complete", json={"completion_date": date.today().isoformat(), "labour_cost": "100", "odometer": 60100}, headers=h)
    client.post(f"/api/vehicle-work-orders/{w.json()['id']}/close", headers=fx["boss"])
    after = client.get("/api/vehicle-plans", params={"vehicle_id": v["id"]}, headers=h).json()["items"][0]
    assert after["last_done_odometer"] == 60100 and after["km_left"] == 10000 and after["overdue"] is False


def test_tyres_by_wheel_position(h, fx):
    v = ready_vehicle(h, fx)
    serial = lambda: f"T{uuid.uuid4().hex[:7].upper()}"
    assert client.post("/api/tyres", json={"serial_number": serial(), "brand": "Unknown brand"}, headers=h).status_code == 422
    a = client.post("/api/tyres", json={"serial_number": serial(), "brand": fx["brand"], "category": fx["tcat"], "purchase_cost": "20000", "tread_depth_mm": "16", "warehouse": fx["wh"], "size": "295/80R22.5"}, headers=h).json()
    assert client.post("/api/tyres", json={"serial_number": a["serial_number"].lower()}, headers=h).status_code == 409
    b = client.post("/api/tyres", json={"serial_number": serial(), "brand": fx["brand"], "purchase_cost": "18000"}, headers=h).json()
    layout = client.get(f"/api/vehicles/{v['id']}/tyres", headers=h).json()
    assert [x["position"] for x in layout["layout"]] == ["Steer-L", "Steer-R", "Drive-LO", "Drive-LI", "Drive-RI", "Drive-RO"] and all(x["tyre"] is None for x in layout["layout"])
    assert client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": v["id"], "position": "Roof", "odometer": 1100}, headers=h).status_code == 422
    assert client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": v["id"], "position": "steer-l", "odometer": 500}, headers=h).status_code == 409            # odometer back
    f = client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": v["id"], "position": "steer-l", "odometer": 1100, "tread_depth_mm": "15.5"}, headers=h)
    assert f.status_code == 200 and f.json()["status"] == "FITTED" and f.json()["current_position"] == "Steer-L"
    assert client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": v["id"], "position": "Steer-R", "odometer": 1100}, headers=h).status_code == 409           # already on the vehicle
    other = ready_vehicle(h, fx)
    assert client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": other["id"], "position": "Steer-R", "odometer": 1100}, headers=h).status_code == 409    # never on two vehicles
    assert client.post(f"/api/tyres/{b['id']}/fit", json={"vehicle_id": v["id"], "position": "Steer-L", "odometer": 1100}, headers=h).status_code == 409           # position taken
    assert client.get(f"/api/vehicles/{v['id']}/tyres", headers=h).json()["layout"][0]["tyre"]["serial_number"] == a["serial_number"]
    assert client.post(f"/api/tyres/{a['id']}/rotate", json={"position": "Steer-R", "odometer": 6100}, headers=h).json()["current_position"] == "Steer-R"
    r = client.post(f"/api/tyres/{a['id']}/remove", json={"odometer": 11100, "to_status": "RETREADING", "reason": "worn, send to retreader", "tread_depth_mm": "3", "cost": "4000"}, headers=h).json()
    assert r["status"] == "RETREADING" and r["total_km"] == 10000 and D(r["total_cost"]) == 24000 and D(r["cost_per_km"]) == D("2.4")
    back = client.post(f"/api/tyres/{a['id']}/receive", json={"tread_depth_mm": "14", "warehouse": fx["wh"], "cost": "500"}, headers=h).json()
    assert back["status"] == "IN_STOCK" and back["retread_count"] == 1 and back["condition_type"] == "RETREAD"
    hist = client.get(f"/api/tyres/{a['id']}", headers=h).json()["history"]
    assert [e["event"] for e in hist][::-1] == ["RECEIVED", "FITTED", "ROTATED", "REMOVED", "RETREAD_BACK"]
    assert client.post(f"/api/tyres/{a['id']}/scrap", json={"reason": "retread failed"}, headers=fx["viewer"]).status_code == 403
    assert client.post(f"/api/tyres/{a['id']}/scrap", json={"reason": "retread failed"}, headers=h).json()["status"] == "SCRAPPED"
    assert client.post(f"/api/tyres/{a['id']}/fit", json={"vehicle_id": v["id"], "position": "Steer-L", "odometer": 12000}, headers=h).status_code == 409


def test_a_vehicle_without_defined_wheel_positions_cannot_take_tyres(h, fx):
    v = ready_vehicle(h, fx, axle_config=fx["noaxle"])
    t = client.post("/api/tyres", json={"serial_number": f"T{uuid.uuid4().hex[:7].upper()}"}, headers=h).json()
    r = client.post(f"/api/tyres/{t['id']}/fit", json={"vehicle_id": v["id"], "position": "Steer-L", "odometer": 1100}, headers=h)
    assert r.status_code == 409 and "Position Labels" in r.json()["detail"]


def test_fuel_economy_abnormal_fills_and_summary(h, fx):
    v = ready_vehicle(h, fx, odometer=0)
    fill = lambda odo, litres, **kw: client.post("/api/fuel", json={"vehicle_id": v["id"], "txn_date": "2026-11-05", "odometer": odo, "litres": litres, "amount": str(D(litres) * 90), **kw}, headers=h)
    assert fill(1000, "100").json()["km_per_litre"] is None                                          # the first fill has nothing to compare with
    for odo in (1400, 1800, 2200, 2600):
        assert D(fill(odo, "100").json()["km_per_litre"]) == 4
    bad = fill(2800, "100").json()                                                                    # 200 km on 100 litres = 2 km/l, far below the 4 km/l average
    assert D(bad["km_per_litre"]) == 2 and bad["abnormal"] is True
    assert fill(2000, "50").status_code == 409                                                        # odometer lower than the vehicle's
    assert client.post("/api/fuel", json={"vehicle_id": v["id"], "txn_date": "2026-11-05", "litres": "40", "kind": "FUEL"}, headers=h).status_code == 422   # fuel needs an odometer
    assert client.post("/api/fuel", json={"vehicle_id": v["id"], "txn_date": "2026-11-05", "litres": "5", "kind": "LUBRICANT", "amount": "900"}, headers=h).status_code == 201
    s = next(x for x in client.get("/api/fuel/summary", headers=h).json()["items"] if x["vehicle_id"] == v["id"])
    assert s["fills"] == 6 and s["abnormal_fills"] == 1 and s["km"] == 1800 and D(s["litres"]) == 600
    assert client.get("/api/fuel", params={"vehicle_id": v["id"], "abnormal": True}, headers=h).json()["total"] == 1
    assert get(h, v)["odometer"] == 2800


def test_alerts_and_summary_come_from_real_records(h, fx):
    v = new_vehicle(h, fx, odometer=900)
    doc(h, v, fx["fit"], days=20)
    al = client.get("/api/fleet-alerts", params={"days": 30}, headers=h).json()
    mine = [x for x in al["documents"] if x["registration_number"] == v["registration_number"]]
    assert mine and mine[0]["days_left"] == 20 and mine[0]["expired"] is False
    assert not [x for x in client.get("/api/fleet-alerts", params={"days": 10}, headers=h).json()["documents"] if x["registration_number"] == v["registration_number"]]
    t = client.post("/api/tyres", json={"serial_number": f"T{uuid.uuid4().hex[:7].upper()}", "tread_depth_mm": "2"}, headers=h).json()
    client.post(f"/api/tyres/{t['id']}/fit", json={"vehicle_id": v["id"], "position": "Drive-LO", "odometer": 1000}, headers=h)
    assert any(x["serial_number"] == t["serial_number"] for x in client.get("/api/fleet-alerts", headers=h).json()["tyres_low_tread"])
    s = client.get("/api/vehicles/summary", headers=h).json()
    assert s["total"] >= 1 and s["by_status"].get("AVAILABLE", 0) >= 1 and s["tyres_by_status"].get("FITTED", 0) >= 1


def test_a_viewer_can_look_but_not_change_anything(h, fx):
    v = ready_vehicle(h, fx)
    vh = fx["viewer"]
    assert client.get("/api/vehicles", headers=vh).status_code == 200 and client.get(f"/api/vehicles/{v['id']}", headers=vh).status_code == 200
    for method, url, body in (("post", "/api/vehicles", {"registration_number": "TN09XX0001"}), ("post", f"/api/vehicles/{v['id']}/allocate", {}), ("post", "/api/vehicle-work-orders", {"vehicle_id": v["id"], "complaint": "x"}),
                              ("post", "/api/tyres", {"serial_number": "ZZ99"}), ("post", "/api/fuel", {"vehicle_id": v["id"], "txn_date": "2026-11-05", "litres": "5", "kind": "LUBRICANT"})):
        assert getattr(client, method)(url, json=body, headers=vh).status_code == 403, url
    assert client.get("/api/vehicles/99999999", headers=h).status_code == 404
