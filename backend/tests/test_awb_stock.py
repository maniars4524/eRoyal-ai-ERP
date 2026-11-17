"""Master AWB numbers of air exports come from the Air Stock (Airline Stock Line Master); a number is used once."""
import uuid

import pytest

from tests.test_billing import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def mk_air(h, direction="Export"):
    r = client.post("/api/shipments", json={"mode": "Air", "direction": direction, "pol": "INMAA", "pod": "SGSIN"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["job_number"]


def put_bl(h, job, mawb, airline="UL", bt="Direct"):
    return client.put(f"/api/jobs/{job}/details/bl", json={"data": {"bookingType": bt, "mblNo": mawb, "awb": {"airline": airline}}}, headers=h)


def test_master_awb_comes_from_the_air_stock_and_is_used_once(h):
    tag = uuid.uuid4().hex[:3].upper()
    al = client.post("/api/network/airlines", json={"code": "Z" + tag[:2], "name": "Stock Air " + tag, "num_code": "603"}, headers=h)
    assert al.status_code == 201, al.text
    aid = al.json()["id"]
    code = al.json()["code"]
    assert client.post("/api/network/stock-lines", json={"airline_id": aid, "bill_type": "MASTER", "from_awb": "60134700", "to_awb": "60134704"}, headers=h).status_code == 201
    assert client.post("/api/network/stock-lines", json={"airline_id": aid, "bill_type": "HOUSE", "from_awb": "11110000", "to_awb": "11110009"}, headers=h).status_code == 201      # a HOUSE range never offers a master number
    a, b = mk_air(h), mk_air(h)
    assert put_bl(h, a, "", code).status_code == 200
    st = client.get(f"/api/jobs/{a}/awb-stock", headers=h).json()
    assert st["airline"]["code"] == code and st["prefix"] == "603" and st["has_stock"] is True
    assert st["available"] == [f"603-6013470{i}" for i in range(5)] and st["ranges"][0]["free"] == 5
    assert put_bl(h, a, "603-60134701", code).status_code == 200                                         # a number of the stock is accepted
    st_b = client.get(f"/api/jobs/{b}/awb-stock?airline={code}", headers=h).json()
    assert "603-60134701" not in st_b["available"] and len(st_b["available"]) == 4                         # and is no longer offered to another job
    r = put_bl(h, b, "603 60134701", code)
    assert r.status_code == 409 and a in r.text                                                           # taking it twice is refused
    r = put_bl(h, b, "603-99999999", code)
    assert r.status_code == 200                                                                           # a number outside the stock may be typed by hand (manual entry)
    r = put_bl(h, mk_air(h), "603-99999999", code)
    assert r.status_code == 409                                                                           # ... but not one another job has taken
    assert put_bl(h, a, "603-60134701", code).status_code == 200                                         # saving the same number again is fine
    # consol: House jobs may share one Master AWB with each other, but not with a Direct job
    c1, c2 = mk_air(h), mk_air(h)
    assert put_bl(h, mk_air(h), "603-60134702", "").status_code == 422                                  # no airline chosen: no number
    assert put_bl(h, c1, "603-60134703", code, "House").status_code == 200
    assert put_bl(h, c2, "603-60134703", code, "House").status_code == 200
    assert put_bl(h, b, "603-60134703", code).status_code == 409
    assert client.get(f"/api/jobs/{a}/awb-stock", headers=h).json()["current"] == "603-60134701"


def test_other_jobs_and_airlines_without_stock_are_not_affected(h):
    imp, sea = mk_air(h, "Import"), client.post("/api/shipments", json={"mode": "Sea", "direction": "Export", "pol": "INMAA", "pod": "SGSIN"}, headers=h).json()["job_number"]
    assert put_bl(h, imp, "123-45678901", "UL").status_code == 200                                         # an air import job keeps typing its master number
    assert client.put(f"/api/jobs/{sea}/details/bl", json={"data": {"bookingType": "House", "mblNo": "MSCU12345", "awb": {}}}, headers=h).status_code == 200
    exp = mk_air(h)
    assert put_bl(h, exp, "020-12345675", "NOAIRLINE").status_code == 200                                  # an airline that is not in the master / has no stock can still be typed
    st = client.get(f"/api/jobs/{exp}/awb-stock", headers=h).json()
    assert st["airline"] is None and st["available"] == []
