from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .auth_api import platform_router, router as auth_router
from .customers_api import router as customers_router
from .dashboard_api import router as dashboard_router
from .documents_api import router as documents_router
from .job_charges_api import router as job_charges_router
from .payments_api import router as payments_router
from .quotations_api import router as quotations_router
from .masters_api import router as masters_router
from .users_api import router as users_router
from .ports_api import router as ports_router
from .shipments_api import router as shipments_router
from .config import CORS_ORIGINS, IS_PRODUCTION, PUBLIC_PORTALS
from .db import Base, engine
from . import models

UI_FILE = Path(__file__).resolve().parents[2] / "esway-erp.html"

app = FastAPI(title="eSway ERP 2.0 API", version="2.0.0", docs_url=None if PUBLIC_PORTALS else "/docs", redoc_url=None if PUBLIC_PORTALS else "/redoc", openapi_url=None if PUBLIC_PORTALS else "/openapi.json")


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
app.include_router(masters_router)
app.include_router(users_router)
app.include_router(shipments_router)
app.include_router(job_charges_router)
app.include_router(documents_router)
app.include_router(dashboard_router)
app.include_router(payments_router)
app.include_router(quotations_router)


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



LANDING_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>eSway ERP 2.0</title><style>
:root{--bg:#0b1f3a;--card:#fff;--ink:#0b1f3a;--mut:#5b6b82;--acc:#1a6fbf}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);font-family:system-ui,Segoe UI,Arial,sans-serif;padding:16px}
main{width:100%;max-width:880px}h1{color:#fff;text-align:center;margin:0 0 4px;font-size:28px}p.s{color:#a9bbd3;text-align:center;margin:0 0 24px}
.g{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
a{background:var(--card);color:var(--ink);border-radius:12px;padding:20px;text-decoration:none;display:block}
a:hover{outline:3px solid var(--acc)}b{font-size:17px;display:block;margin-bottom:6px}span{color:var(--mut);font-size:13.5px;line-height:1.4}
</style></head><body><main><h1>eSway ERP 2.0</h1><p class="s">Choose your portal</p><div class="g">
<a href="/erp/login"><b>ERP</b><span>Employees and business users. Operations, finance and reports.</span></a>
<a href="/demo/login"><b>Demo</b><span>Explore the system with sample data. No production data.</span></a>
<a href="/developer/login"><b>Developer / Admin</b><span>System administration, companies, users and security.</span></a>
</div></main></body></html>"""


PORTAL_CARDS = {
    "erp": ("ERP", "Employees and business users. Operations, finance and reports."),
    "demo": ("Demo", "Explore the system with sample data. No production data."),
    "developer": ("Developer / Admin", "System administration, companies, users and security."),
}


def _landing(allowed: list[str]) -> str:
    cards = "".join(f'<a href="/{p}/login"><b>{PORTAL_CARDS[p][0]}</b><span>{PORTAL_CARDS[p][1]}</span></a>' for p in allowed)
    return LANDING_HTML.split('<div class="g">')[0] + '<div class="g">' + cards + "</div></main></body></html>"


def _ui_response() -> FileResponse:
    return FileResponse(UI_FILE, media_type="text/html", headers={"Cache-Control": "no-store"})


_allowed = [p for p in ("erp", "demo", "developer") if not PUBLIC_PORTALS or p in PUBLIC_PORTALS]


@app.get("/", include_in_schema=False)
def landing() -> HTMLResponse:
    return HTMLResponse(_landing(_allowed), headers={"Cache-Control": "no-store"})


for _p in _allowed:
    app.add_api_route(f"/{_p}", _ui_response, methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_p}/login", _ui_response, methods=["GET"], include_in_schema=False)
if not PUBLIC_PORTALS:
    app.add_api_route("/prototype", _ui_response, methods=["GET"], include_in_schema=False)
