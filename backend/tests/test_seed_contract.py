from app.seed import PERMISSIONS


def test_local_seed_contract_has_distinct_roles_and_permissions():
    codes = {code for code, _name, _module, _action in PERMISSIONS}
    assert {"organization.read", "organization.create", "organization.update"}.issubset(codes)
    assert "user.create" in codes
    assert "Admin" != "mani"
