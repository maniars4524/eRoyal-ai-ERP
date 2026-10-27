import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASSWORD = "Portal-Test-Passw0rd!"


@pytest.fixture(scope="module")
def h(world, admin):
    r = client.post("/api/auth/login", json={"identifier": admin["username"], "password": PASSWORD, "portal": "erp", "license_number": admin["license"]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_the_theme_is_saved_on_the_account_and_comes_back(h):
    assert client.get("/api/auth/me", headers=h).json()["theme"] is None
    assert client.put("/api/auth/preferences", json={"theme": "burgundy"}, headers=h).json() == {"theme": "burgundy", "custom_colors": None}
    assert client.get("/api/auth/me", headers=h).json()["theme"] == "burgundy"
    assert client.put("/api/auth/preferences", json={"theme": "hot-pink"}, headers=h).status_code == 422  # only the eight themes
    assert client.put("/api/auth/preferences", json={"theme": "midnight"}, headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).json()["theme"] == "midnight"
