"""EIR form details (as the printed receipt), photo / document attachments and the CSC / lease expiry window."""
import base64
from datetime import date, timedelta

import pytest

from tests.test_bookings import client
from tests.test_containers import APPROVER, h, move, new_container, ok, setup  # noqa: F401
from tests.test_container_ops import A, at_depot, lease_body, ops  # noqa: F401

PNG = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")).decode()
PDF = base64.b64encode(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF").decode()


def issue_eir(h, ops, details):
    c = new_container(h, ops)
    mv = move(h, c, "IN", "RECEIPT", to_depot=ops["chn"], truck_number="TN09AA1111").json()
    ok(mv)
    r = client.post("/api/container-eirs", json={"movement_id": mv["id"], "details": details, "driver_signed_by": "Driver A"}, headers=h)
    assert r.status_code == 201, r.text
    return c, mv, r.json()


def test_eir_keeps_the_boxes_of_the_printed_form_and_drops_the_rest(h, ops):
    details = {"load_status": "EMPTY", "reposition_for_maintenance": True, "custom_seal": "CS123", "line_seal": "MSC999", "shipper_seal": "SH77", "set_temp": "-18", "read_temp": "-17.5", "fuel": "3/4",
               "chassis_no": "CH-1", "genset_no": "GS-9", "tires": {"lo_front": "OK", "ri_rear": "13", "bogus": "x"}, "tattle_caps_missing": {"left_f": True, "nope": True},
               "chassis_check": {"Brakes": "DEFECTIVE", "Legs": "OK", "Rim": "BROKEN-VALUE"}, "container_codes": ["01", "10", "abc"], "tire_codes": ["13", "20"], "time_in": "10:30",
               "carrier": "Acme Haulage", "shipping_agent": "MSC Agents", "container_remarks": "scratch on door", "evil": "<script>"}
    c, mv, e = issue_eir(h, ops, details)
    d = e["details"]
    assert d["load_status"] == "EMPTY" and d["reposition_for_maintenance"] is True and d["custom_seal"] == "CS123" and d["chassis_check"] == {"Brakes": "DEFECTIVE", "Legs": "OK"}
    assert d["tires"] == {"lo_front": "OK", "ri_rear": "13"} and d["tattle_caps_missing"] == {"left_f": True} and d["container_codes"] == ["01", "10"] and d["tire_codes"] == ["13", "20"]
    assert "evil" not in d and e["truck_number"] == "TN09AA1111"
    assert client.get(f"/api/container-eirs/by-movement/{mv['id']}", headers=h).json()["details"]["carrier"] == "Acme Haulage"
    fix = A(f"/api/container-eirs/{e['id']}", {"movement_id": mv["id"], "details": {"custom_seal": "CS124"}, "driver_signed_by": "Driver B"}, method="patch")
    assert fix.status_code == 200 and fix.json()["details"] == {"custom_seal": "CS124"} and fix.json()["driver_signed_by"] == "Driver B"


def test_photos_and_documents_can_be_attached_listed_downloaded_and_removed_by_approvers(h, ops):
    c = new_container(h, ops)
    up = lambda **kw: client.post("/api/container-attachments", json={"entity_type": "container", "entity_id": c["id"], "name": "dent.png", "content_type": "image/png", "data_base64": PNG, **kw}, headers=h)
    r = up(caption="roof dent")
    assert r.status_code == 201 and r.json()["category"] == "PHOTO" and r.json()["size"] > 50, r.text
    pdf = up(name="survey.pdf", content_type="application/pdf", data_base64=PDF)
    assert pdf.status_code == 201 and pdf.json()["category"] == "DOCUMENT"
    assert up(content_type="application/x-msdownload").status_code == 422                      # only photos and PDFs
    assert up(data_base64=PDF).status_code == 422                                              # a PDF pretending to be a PNG
    assert up(data_base64="not base64!!").status_code == 422
    assert client.post("/api/container-attachments", json={"entity_type": "container", "entity_id": 99999999, "name": "x.png", "content_type": "image/png", "data_base64": PNG}, headers=h).status_code == 404
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)).decode()
    assert up(data_base64=big).status_code == 422
    rows = client.get("/api/container-attachments", params={"entity_type": "container", "entity_id": c["id"]}, headers=h).json()
    assert [x["name"] for x in rows] == ["dent.png", "survey.pdf"]
    f = client.get(f"/api/container-attachments/{rows[0]['id']}/file", headers=h)
    assert f.status_code == 200 and f.headers["content-type"] == "image/png" and f.content == base64.b64decode(PNG)
    assert client.delete(f"/api/container-attachments/{rows[0]['id']}", headers=h).status_code == 204
    assert client.delete(f"/api/container-attachments/{rows[1]['id']}", headers=APPROVER["h"]).status_code == 204
    assert client.get("/api/container-attachments", params={"entity_type": "container", "entity_id": c["id"]}, headers=h).json() == []
    assert client.get(f"/api/container-attachments/{rows[0]['id']}/file", headers=h).status_code == 404


def test_photos_attach_to_surveys_repairs_and_eirs_too(h, ops):
    c = at_depot(h, ops)
    s = client.post("/api/container-surveys", json={"container_id": c["id"], "survey_type": ops["survey"], "survey_date": "2026-11-05", "damage_description": "dent"}, headers=h).json()
    w = client.post("/api/container-repairs", json={"container_id": c["id"], "labour": "100"}, headers=h).json()
    for kind, rid in (("survey", s["id"]), ("repair", w["id"])):
        assert client.post("/api/container-attachments", json={"entity_type": kind, "entity_id": rid, "name": "p.png", "content_type": "image/png", "data_base64": PNG}, headers=h).status_code == 201, kind


def test_expiry_window_controls_what_the_csc_and_lease_alerts_show(h, ops):
    c = new_container(h, ops)
    client.patch(f"/api/containers/{c['id']}", json={"csc_valid_until": (date.today() + timedelta(days=100)).isoformat()}, headers=h)
    has = lambda days: any(x["container_number"] == c["container_number"] for x in client.get("/api/container-alerts", params={"days": days}, headers=h).json()["csc"])
    assert not has(60) and has(120)
    item = next(x for x in client.get("/api/container-alerts", params={"days": 120}, headers=h).json()["csc"] if x["container_number"] == c["container_number"])
    assert item["days_left"] == 100 and item["expired"] is False
    lease = client.post("/api/container-leases", json=lease_body(end_date=(date.today() + timedelta(days=100)).isoformat()), headers=h).json()
    lc = new_container(h, ops, ownership="lease")
    client.post(f"/api/container-leases/{lease['id']}/on-hire", json={"container_id": lc["id"], "on_hire_date": "2026-02-01"}, headers=h)
    seen = lambda days: any(x["agreement_number"] == lease["agreement_number"] for x in client.get("/api/container-alerts", params={"days": days}, headers=h).json()["leases"])
    assert not seen(30) and seen(120)
