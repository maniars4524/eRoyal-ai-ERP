from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .api import router
from .auth_api import platform_router, router as auth_router
from .customers_api import router as customers_router
from .ports_api import router as ports_router
from .config import CORS_ORIGINS
from .db import Base, engine
from . import models

app = FastAPI(title="eRoyal Freight ERP API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(auth_router)
app.include_router(platform_router)
app.include_router(customers_router)
app.include_router(ports_router)


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
