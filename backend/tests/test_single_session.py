from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


def test_second_sign_in_must_confirm_and_ends_the_first(world, admin, monkeypatch):
    monkeypatch.setattr(config, "SINGLE_SESSION", True)
    body = {"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]}
    first = client.post("/api/auth/login", json=body)
    assert first.status_code == 200
    h1 = {"Authorization": "Bearer " + first.json()["access_token"]}
    assert client.get("/api/auth/me", headers=h1).status_code == 200
    # another computer: refused with a warning until the person confirms
    again = client.post("/api/auth/login", json=body)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_SIGNED_IN"
    assert client.get("/api/auth/me", headers=h1).status_code == 200  # the warning alone does not end the first session
    second = client.post("/api/auth/login", json={**body, "replace_session": True})
    assert second.status_code == 200
    h2 = {"Authorization": "Bearer " + second.json()["access_token"]}
    old = client.get("/api/auth/me", headers=h1)
    assert old.status_code == 401 and "signed in on another computer" in old.json()["detail"]
    assert client.get("/api/auth/me", headers=h2).status_code == 200
    # signing out frees the account: the next sign-in needs no confirmation
    assert client.post("/api/auth/logout", headers=h2).status_code == 200
    assert client.post("/api/auth/login", json=body).status_code == 200
