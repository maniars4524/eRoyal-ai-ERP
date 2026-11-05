"""NVOCC container inventory: ISO 6346 numbers, the register, status history, IN / OUT movements with approval, reversal and the never-in-two-places rule."""
import uuid

import pytest
from sqlalchemy import select

from app.containers_api import check_digit, validate_number
from app.db import SessionLocal
from app.models import Organization, Permission, Role, User
from app.security import hash_password
from tests.test_bookings import client, hdr


def with_check(prefix10: str) -> str:
    return prefix10 + str(check_digit(prefix10))


def number(tag_digits: str, owner="TST") -> str:
    return with_check(f"{owner}U{tag_digits}")


def test_iso_6346_check_digit_and_format():
    assert validate_number("CSQU3054383") == "CSQU3054383"
    assert validate_number("csqu 305438-3") == "CSQU3054383"
    with pytest.raises(Exception) as bad:
        validate_number("CSQU3054384")
    assert "must end with 3" in str(bad.value.detail)
    for wrong in ("CSQX3054383", "CSQU305438", "1234567890A", "CSQU30543833"):
        with pytest.raises(Exception):
            validate_number(wrong)


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def make_user(world, label, perms):
    db = SessionLocal()
    org = db.get(Organization, world["erp_org"])
    tag = uuid.uuid4().hex[:6]
    role = Role(name=f"CT{label}{tag}", organization_id=org.id)
    role.permissions = list(db.scalars(select(Permission).where(Permission.code.in_(perms))))
    db.add(role)
    u = User(email=f"{label}-{tag}@users.test", username=f"{label}{tag}", password_hash=hash_password("Portal-Test-Passw0rd!"), first_name=label, organization_id=org.id, status="ACTIVE")
    u.roles.append(role)
    db.add(u)
    db.commit()
    out = {"id": u.id, "username": u.username, "license": org.license_number}
    db.close()
    return out


@pytest.fixture(scope="module")
def setup(h, world):
    tag = uuid.uuid4().hex[:5].upper()

    def m(t, code, name, data=None):
        return client.post(f"/api/masters/{t}", json={"code": f"{code}{tag}", "name": f"{name} {tag}", "data": data or {}}, headers=h)

    for r in (m("inv_container_size", "S20", "20 FT", {"length_feet": "20"}), m("inv_container_ownership", "OWN", "Company owned", {"treatment": "Company fixed asset"}),
              m("inv_container_ownership", "LSE", "Leased", {"treatment": "Leased (not a company asset)"}), m("inv_container_grade", "G1", "Grade 1", {"grade_rank": "1"}),
              m("inv_container_condition", "GOOD", "Good", {"available_for_use": "Yes"}), m("inv_container_condition", "DMG", "Damaged", {"available_for_use": "No"}),
              m("inv_depot", "CHN", "Chennai"), m("inv_depot", "MUM", "Mumbai"), m("inv_movement_type", "RCV", "Empty received", {"direction": "IN"}),
              m("inv_movement_type", "REL", "Empty released", {"direction": "OUT"})):
        assert r.status_code == 201, r.text
    assert m("inv_yard_location", "A01", "Chennai A01", {"depot": f"Chennai {tag}"}).status_code == 201
    ap = make_user(world, "approver", ["container.read", "container.create", "container.approve"])
    APPROVER["h"] = hdr(ap["username"], ap["license"])
    assert m("inv_yard_location", "B01", "Mumbai B01", {"depot": f"Mumbai {tag}"}).status_code == 201
    return {"size": f"20 FT {tag}", "own": f"Company owned {tag}", "lease": f"Leased {tag}", "grade": f"Grade 1 {tag}", "good": f"Good {tag}", "dmg": f"Damaged {tag}",
            "chn": f"Chennai {tag}", "mum": f"Mumbai {tag}", "inmt": f"Empty received {tag}", "outmt": f"Empty released {tag}", "tag": tag}


_seq = iter(range(100000, 999999))


def new_container(h, names, ownership="own", condition="good"):
    num = number(f"{next(_seq):06d}")
    r = client.post("/api/containers", json={"container_number": num, "size": names["size"], "ownership": names[ownership], "grade": names["grade"], "condition": names[condition]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def move(h, c, direction, purpose, **kw):
    body = {"container_id": c["id"], "direction": direction, "purpose": purpose, "movement_date": "2026-11-05", **kw}
    return client.post("/api/container-movements", json=body, headers=h)


def approve(h, m):
    return client.post(f"/api/container-movements/{m['id']}/approve", headers=h)


APPROVER = {}


def ok(m):
    """Approve through a separate approver (the admin who creates movements must not approve their own once other approvers exist)."""
    return approve(APPROVER["h"], m)


def get(h, c):
    return client.get(f"/api/containers/{c['id']}", headers=h).json()


def test_register_rejects_bad_numbers_duplicates_and_unknown_masters(h, setup):
    assert client.post("/api/containers", json={"container_number": "CSQU3054384"}, headers=h).status_code == 422
    c = new_container(h, setup)
    assert c["availability"] == "NOT_AVAILABLE" and c["location_status"] == "NOT_AT_DEPOT" and c["is_company_asset"] is True
    assert client.post("/api/containers", json={"container_number": c["container_number"]}, headers=h).status_code == 409
    r = client.post("/api/containers", json={"container_number": number("999001"), "size": "40 HC unknown"}, headers=h)
    assert r.status_code == 422 and "Masters > Inventory" in r.json()["detail"]
    lease = new_container(h, setup, ownership="lease")
    assert lease["is_company_asset"] is False                      # a leased container is never treated as a company asset


def test_in_movement_changes_nothing_until_approved_then_places_the_container(h, setup):
    c = new_container(h, setup)
    r = move(h, c, "IN", "RECEIPT", to_depot=setup["chn"], to_location=f"A01{setup['tag']}", movement_type=setup["inmt"], truck_number="TN01AB1234")
    assert r.status_code == 201, r.text
    mv = r.json()
    assert mv["status"] == "DRAFT" and mv["number"]
    assert get(h, c)["location_status"] == "NOT_AT_DEPOT"
    assert ok(mv).status_code == 200
    now = get(h, c)
    assert (now["depot"], now["location_status"], now["availability"], now["yard_location"]) == (setup["chn"], "AT_DEPOT", "AVAILABLE", f"A01{setup['tag']}")
    assert {"depot", "location_status", "availability"} <= {x["field"] for x in now["history"]}
    assert ok(mv).status_code == 409                          # not twice


def test_never_at_two_depots_and_one_open_draft_per_container(h, setup):
    c = new_container(h, setup)
    m1 = move(h, c, "IN", "RECEIPT", to_depot=setup["chn"]).json()
    assert move(h, c, "IN", "RECEIPT", to_depot=setup["mum"], movement_date="2026-11-06").status_code == 409      # a draft is already waiting
    ok(m1)
    assert move(h, c, "IN", "RECEIPT", to_depot=setup["mum"], movement_date="2026-11-06").status_code == 409      # already at Chennai
    out = move(h, c, "OUT", "TRANSFER", to_depot=setup["mum"], movement_date="2026-11-06").json()
    ok(out)
    assert get(h, c)["location_status"] == "IN_TRANSIT" and get(h, c)["depot"] is None
    assert move(h, c, "OUT", "RELEASE", movement_date="2026-11-07").status_code == 409                              # not at a depot while in transit
    inn = move(h, c, "IN", "TRANSFER", to_depot=setup["mum"], movement_date="2026-11-07").json()
    ok(inn)
    assert get(h, c)["depot"] == setup["mum"]


def test_release_needs_an_available_container(h, setup):
    c = new_container(h, setup)
    ok(move(h, c, "IN", "RECEIPT", to_depot=setup["chn"]).json())
    assert client.post(f"/api/containers/{c['id']}/status", json={"condition": setup["dmg"], "reason": "dent on roof"}, headers=h).status_code == 200
    now = get(h, c)
    assert now["condition"] == setup["dmg"] and now["availability"] == "NOT_AVAILABLE"
    r = move(h, c, "OUT", "RELEASE", movement_date="2026-11-08")
    assert r.status_code == 409 and "cannot be released" in r.json()["detail"]
    assert move(h, c, "OUT", "REPAIR", movement_date="2026-11-08").status_code == 201                                  # but it can go for repair


def test_release_then_reversal_restores_and_blocks_when_a_later_movement_exists(h, setup):
    c = new_container(h, setup)
    ok(move(h, c, "IN", "RECEIPT", to_depot=setup["chn"], to_location=f"A01{setup['tag']}").json())
    out = move(h, c, "OUT", "RELEASE", movement_date="2026-11-09", customer="Acme").json()
    ok(out)
    assert (get(h, c)["location_status"], get(h, c)["availability"]) == ("WITH_PARTY", "IN_USE")
    r = client.post(f"/api/container-movements/{out['id']}/reverse", json={"reason": "wrong container released"}, headers=h)
    assert r.status_code == 200
    back = get(h, c)
    assert (back["depot"], back["location_status"], back["availability"], back["yard_location"]) == (setup["chn"], "AT_DEPOT", "AVAILABLE", f"A01{setup['tag']}")
    assert any("Reversal" in (x["reason"] or "") for x in back["history"])
    other = new_container(h, setup)
    first = move(h, other, "IN", "RECEIPT", to_depot=setup["chn"]).json()
    ok(first)
    second = move(h, other, "OUT", "RELEASE", movement_date="2026-11-10").json()
    ok(second)
    assert client.post(f"/api/container-movements/{first['id']}/reverse", json={"reason": "no way"}, headers=h).status_code == 409


def test_disposed_containers_cannot_move_again(h, setup):
    c = new_container(h, setup)
    ok(move(h, c, "IN", "RECEIPT", to_depot=setup["chn"]).json())
    ok(move(h, c, "OUT", "DISPOSAL", movement_date="2026-11-11").json())
    assert get(h, c)["location_status"] == "DISPOSED"
    r = move(h, c, "IN", "RECEIPT", to_depot=setup["mum"], movement_date="2026-11-12")
    assert r.status_code == 409 and "disposed" in r.json()["detail"]


def test_movement_type_direction_depot_and_yard_location_are_validated(h, setup):
    c = new_container(h, setup)
    assert move(h, c, "IN", "RECEIPT", to_depot=setup["chn"], movement_type=setup["outmt"]).status_code == 422        # an OUT type used for IN
    assert move(h, c, "IN", "RECEIPT", to_depot="Nowhere").status_code == 422
    assert move(h, c, "IN", "RECEIPT", to_depot=setup["chn"], to_location=f"B01{setup['tag']}").status_code == 422     # B01 is in Mumbai
    assert move(h, c, "IN", "RELEASE", to_depot=setup["chn"]).status_code == 422                                      # wrong purpose for IN
    assert move(h, c, "IN", "RECEIPT").status_code == 422


def test_cancel_and_approval_permissions_and_segregation(h, world, setup):
    clerk = make_user(world, "clerk", ["container.read", "container.create"])
    boss = make_user(world, "boss", ["container.read", "container.create", "container.approve"])
    viewer = make_user(world, "viewer", ["container.read"])
    hc, hb = hdr(clerk["username"], clerk["license"]), hdr(boss["username"], boss["license"])
    c = new_container(h, setup)
    m1 = move(hc, c, "IN", "RECEIPT", to_depot=setup["chn"]).json()
    assert approve(hc, m1).status_code == 403                                         # a clerk cannot approve
    assert approve(hb, m1).status_code == 200
    c2 = new_container(h, setup)
    mine = move(hb, c2, "IN", "RECEIPT", to_depot=setup["chn"]).json()
    r = approve(hb, mine)                                                              # another approver (the admin) exists, so the creator may not approve their own
    assert r.status_code == 403 and "another approver" in r.json()["detail"]
    assert ok(mine).status_code == 200
    c3 = new_container(h, setup)
    m3 = move(hc, c3, "IN", "RECEIPT", to_depot=setup["chn"]).json()
    assert client.post(f"/api/container-movements/{m3['id']}/cancel", json={"reason": "wrong depot"}, headers=hc).status_code == 200
    assert client.get(f"/api/container-movements/{m3['id']}", headers=hc).json()["status"] == "CANCELLED"
    assert client.post("/api/containers", json={"container_number": number("888001")}, headers=hdr(viewer["username"], viewer["license"])).status_code == 403


def test_lists_summary_and_filters_come_from_real_records(h, setup):
    c = new_container(h, setup)
    ok(move(h, c, "IN", "RECEIPT", to_depot=setup["mum"]).json())
    s = client.get("/api/containers/summary", headers=h).json()
    row = next(x for x in s["by_depot"] if x["depot"] == setup["mum"])
    assert row["total"] >= 1 and row["available"] >= 1 and s["total"] >= 1
    items = client.get("/api/containers", params={"depot": setup["mum"], "availability": "AVAILABLE"}, headers=h).json()
    assert c["container_number"] in {x["container_number"] for x in items["items"]}
    assert client.get("/api/containers", params={"q": c["container_number"][4:10]}, headers=h).json()["total"] >= 1
    mv = client.get("/api/container-movements", params={"depot": setup["mum"], "status": "APPROVED"}, headers=h).json()
    assert mv["total"] >= 1 and mv["items"][0]["container_number"]
    chk = client.get("/api/containers/check-number", params={"number": "CSQU305438"}, headers=h).json()
    assert chk["expected_check_digit"] == 3 and chk["complete"] == "CSQU3054383"


def test_a_container_of_another_company_is_not_found(h):
    assert client.get("/api/containers/99999999", headers=h).status_code == 404
