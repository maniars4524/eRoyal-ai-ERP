from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .auth_api import platform_router, router as auth_router
from .customers_api import router as customers_router
from .ports_api import router as ports_router
from .shipments_api import router as shipments_router
from .config import CORS_ORIGINS, IS_PRODUCTION
from .db import Base, engine
from . import models

UI_FILE = Path(__file__).resolve().parents[2] / "eRoyal-ai-ERP.html"

app = FastAPI(title="eSway ERP 2.0 API", version="2.0.0")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(platform_router)
app.include_router(customers_router)
app.include_router(ports_router)
app.include_router(shipments_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    database = "unhealthy"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "healthy"
    except SQLAlchemyError:
        pass
    return {"status": "ok" if database == "healthy" else "degraded", "application": "healthy", "database": database, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/dashboard/summary")
def dashboard_summary() -> dict[str, object]:
    return {
        "active_jobs": None,
        "revenue": None,
        "unbilled_shipments": None,
        "pending_delivery_orders": None,
        "source": "unavailable-until-transaction-modules",
    }


@app.get("/", include_in_schema=False)
@app.get("/prototype", include_in_schema=False)
def ui() -> FileResponse:
    """Serve the single-page ERP UI (in development Vite serves it instead)."""
    return FileResponse(UI_FILE, media_type="text/html", headers={"Cache-Control": "no-store"})
