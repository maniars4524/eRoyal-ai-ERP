"""Output Docs: reports listed by mode / direction, generated only on request, versioned, restricted where they show money, stored HTML cleaned."""
import pytest

from tests.test_bookings import client, hdr

HTML = "<div style='color:#123'>Report <b>body</b> for the job, long enough to store</div>"


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def mk(h, mode, direction, cm=None):
    r = client.post("/api/shipments", json={"mode": mode, "direction": direction, "container_mode": cm or ("Air" if mode == "Air" else "FCL"), "pol": "INMAA", "pod": "SGSIN"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["job_number"]


def codes(h, j):
    return [x["code"] for x in client.get(f"/api/jobs/{j}/outdocs", headers=h).json()["reports"]]


def test_each_category_lists_its_own_reports_and_nothing_is_generated(h):
    assert codes(h, mk(h, "Air", "Export")) == ["hawb", "mawb", "loading_confirmation", "pre_alert", "stuffing_report", "transshipment_confirmation", "export_declaration"]
    assert codes(h, mk(h, "Air", "Import")) == ["arrival_notice", "delivery_order", "destuffing_report", "pod", "air_manifest"]
    assert codes(h, mk(h, "Sea", "Export", "FCL")) == ["hbl", "mbl", "sailing_confirmation", "shipment_summary", "pl_report", "pre_alert"]
    assert codes(h, mk(h, "Sea", "Export", "LCL"))[-1] == "stuffing_report"
    assert codes(h, mk(h, "Sea", "Import", "FCL")) == ["can", "delivery_order", "pod", "shipment_summary"]
    assert "destuffing_report" in codes(h, mk(h, "Sea", "Import", "LCL"))
    assert codes(h, mk(h, "Road", "Export")) == []
    j = mk(h, "Sea", "Export")
    assert all(not x["generated"] for x in client.get(f"/api/jobs/{j}/outdocs", headers=h).json()["reports"])


def test_generate_makes_versions_and_keeps_history(h):
    j = mk(h, "Sea", "Export")
    a = client.post(f"/api/jobs/{j}/outdocs", json={"code": "sailing_confirmation", "title": "Sailing Confirmation " + j, "html": HTML}, headers=h)
    assert a.status_code == 201 and a.json()["version"] == 1 and a.json()["status"] == "Generated"
    b = client.post(f"/api/jobs/{j}/outdocs", json={"code": "sailing_confirmation", "html": HTML + "<div>again</div>", "inputs": {"remarks": "x"}}, headers=h)
    assert b.json()["version"] == 2 and b.json()["status"] == "Regenerated"
    rep = next(x for x in client.get(f"/api/jobs/{j}/outdocs", headers=h).json()["reports"] if x["code"] == "sailing_confirmation")
    assert rep["generated"][""]["version"] == 2 and rep["generated"][""]["count"] == 2
    hist = client.get(f"/api/jobs/{j}/outdocs/history?code=sailing_confirmation", headers=h).json()
    assert [x["version"] for x in hist] == [2, 1]
    old = client.get(f"/api/jobs/{j}/outdocs/{hist[1]['id']}", headers=h).json()
    assert "again" not in old["html"] and old["version"] == 1
    assert "OUTDOC_GENERATED" in str(client.get(f"/api/jobs/{j}/activity", headers=h).json())


def test_a_report_that_does_not_apply_is_refused(h):
    j = mk(h, "Sea", "Export", "FCL")
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "stuffing_report", "html": HTML}, headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "can", "html": HTML}, headers=h).status_code == 422


def test_the_arrival_notice_versions_are_separate_and_must_be_chosen(h):
    j = mk(h, "Sea", "Import")
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "can", "html": HTML}, headers=h).status_code == 422
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "can", "variant": "with_rates", "html": HTML}, headers=h).json()["version"] == 1
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "can", "variant": "without_rates", "html": HTML}, headers=h).json()["version"] == 1
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "can", "variant": "with_rates", "html": HTML}, headers=h).json()["version"] == 2
    gen = next(x for x in client.get(f"/api/jobs/{j}/outdocs", headers=h).json()["reports"] if x["code"] == "can")["generated"]
    assert gen["with_rates"]["version"] == 2 and gen["without_rates"]["version"] == 1


def test_stored_html_is_cleaned(h):
    j = mk(h, "Sea", "Export")
    bad = "<div onclick='x()' style='background:url(javascript:1)'>ok<script>alert(1)</script><iframe src='http://e'></iframe><img src='http://evil/x.png'><a href='javascript:1'>link</a> and enough plain text to keep the page</div>"
    r = client.post(f"/api/jobs/{j}/outdocs", json={"code": "sailing_confirmation", "html": bad}, headers=h).json()
    html = client.get(f"/api/jobs/{j}/outdocs/{r['id']}", headers=h).json()["html"]
    for t in ("script", "iframe", "onclick", "javascript", "evil", "<a "):
        assert t not in html
    assert "ok" in html and "link" in html


def test_financial_reports_need_the_billing_right(h, world):
    from tests.test_containers import make_user

    v = make_user(world, "odview", ["shipment.read"])
    hv = hdr(v["username"], v["license"])
    j = mk(h, "Sea", "Export")
    rep = {x["code"]: x for x in client.get(f"/api/jobs/{j}/outdocs", headers=hv).json()["reports"]}
    assert rep["pl_report"]["allowed"] is False and rep["sailing_confirmation"]["allowed"] is True
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "pl_report", "html": HTML}, headers=hv).status_code == 403
    assert client.post(f"/api/jobs/{j}/outdocs", json={"code": "sailing_confirmation", "html": HTML}, headers=hv).status_code == 201
    done = client.post(f"/api/jobs/{j}/outdocs", json={"code": "pl_report", "html": HTML}, headers=h).json()
    assert client.get(f"/api/jobs/{j}/outdocs/{done['id']}", headers=hv).status_code == 403
    k = mk(h, "Sea", "Import")
    assert client.post(f"/api/jobs/{k}/outdocs", json={"code": "can", "variant": "with_rates", "html": HTML}, headers=hv).status_code == 403
    assert client.post(f"/api/jobs/{k}/outdocs", json={"code": "can", "variant": "without_rates", "html": HTML}, headers=hv).status_code == 201
