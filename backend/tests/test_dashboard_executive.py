from tests.test_cha_invpl import _login_user
from tests.test_dashboard import client, hdr


def test_executive_locks_follow_permissions(world, admin):
    full = client.get("/api/dashboard/executive", headers=hdr(admin["username"], admin["license"]))
    assert full.status_code == 200
    d = full.json()
    assert d["locks"] == {"operations": False, "cha": False, "transport": False, "finance": False}
    assert d["can"]["shipment_create"] is True


def test_operations_user_cannot_see_money_and_exporter_sees_nothing(world):
    ops = _login_user(world, "dxops", "OPERATIONS")[1]
    d = client.get("/api/dashboard/executive", headers=ops).json()
    assert d["locks"]["finance"] is True and d["locks"]["operations"] is False
    assert d["money"]["revenue_mtd"] == "0" and d["money"]["ar_outstanding"] == "0"
    exp = _login_user(world, "dxexp", "EXPORTER_IMPORTER")[1]
    d = client.get("/api/dashboard/executive", headers=exp).json()
    assert all(d["locks"].values())
    assert d["jobs"]["active"] == 0


def test_sales_cha_transport_roles_have_their_own_dashboard_locks(world):
    for key, open_, shut in (("SALES", ["operations"], ["cha", "transport", "finance"]), ("CHA", ["operations", "cha"], ["transport", "finance"]), ("TRANSPORT", ["operations", "transport"], ["cha", "finance"])):
        d = client.get("/api/dashboard/executive", headers=_login_user(world, "dx" + key.lower(), key)[1]).json()
        assert all(d["locks"][k] is False for k in open_) and all(d["locks"][k] is True for k in shut), (key, d["locks"])
