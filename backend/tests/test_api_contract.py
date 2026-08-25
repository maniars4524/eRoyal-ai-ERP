from fastapi.testclient import TestClient

from app.main import app


def test_health_distinguishes_database_state():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["application"] == "healthy"
    assert body["database"] in {"healthy", "unhealthy"}
    assert body["status"] in {"ok", "degraded"}


def test_protected_endpoint_requires_authentication():
    response = TestClient(app).get("/api/auth/me")
    assert response.status_code == 401
