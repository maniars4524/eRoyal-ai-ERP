from datetime import datetime, timezone

from pathlib import Path

from fastapi import HTTPException, FastAPI
from fastapi import Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .auth_api import platform_router, router as auth_router
from .customers_api import router as customers_router
from .search_api import router as search_router
from .cha_api import router as cha_router
from .hbl_clauses import router as hbl_clause_router, job_router as hbl_clause_job_router
from .dashboard_api import router as dashboard_router
from .bookings_api import router as bookings_router
from .customs_api import router as customs_router
from .accounts_api import router as accounts_router
from .coa_api import router as coa_router
from .gl_api import router as gl_router
from .mis_api import router as mis_router
from .network_api import router as network_router
from .audit_api import router as audit_router
from .license_mgmt_api import router as license_router
from .job_details_api import company as company_doc_router, router as job_details_router
from .crm_api import router as crm_router
from .org_ext_api import router as org_ext_router
from .jobs_lifecycle_api import router as jobs_lifecycle_router
from .sequences_api import router as sequences_router
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
BRAND_DIR = Path(__file__).resolve().parents[2] / "frontend"
BRAND_FILES = {"logo-sway.png", "logo-sway-white.png"}

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
app.include_router(search_router)
app.include_router(cha_router)
app.include_router(hbl_clause_router)
app.include_router(hbl_clause_job_router)
app.include_router(ports_router)
app.include_router(masters_router)
app.include_router(users_router)
app.include_router(shipments_router)
app.include_router(job_charges_router)
app.include_router(documents_router)
app.include_router(dashboard_router)
app.include_router(payments_router)
app.include_router(quotations_router)
app.include_router(bookings_router)
app.include_router(customs_router)
app.include_router(job_details_router)
app.include_router(company_doc_router)
app.include_router(accounts_router)
app.include_router(coa_router)
app.include_router(gl_router)
app.include_router(mis_router)
app.include_router(network_router)
app.include_router(audit_router)
app.include_router(license_router)
app.include_router(crm_router)
app.include_router(org_ext_router)
app.include_router(jobs_lifecycle_router)
app.include_router(sequences_router)


@app.on_event("startup")
def _sync_template_roles() -> None:
    """New permissions reach existing companies' template roles automatically."""
    try:
        from .seed import seed_super_admin, sync_roles

        seed_super_admin()
        sync_roles()
        from . import std_masters
        from .db import SessionLocal

        with SessionLocal() as db:
            std_masters.seed_all_orgs(db)
    except Exception:  # database unavailable at boot: the health endpoint reports it
        pass


@app.on_event("startup")
async def _license_reminders() -> None:
    """Expiry reminders: catch up at start-up, then look again every few hours. The state lives in the database (one reminder per company, reminder and expiry date), so a restart never repeats or loses one."""
    import asyncio

    from .license_mgmt import run_notification_job

    async def loop() -> None:
        while True:
            await asyncio.to_thread(run_notification_job)
            await asyncio.sleep(6 * 3600)

    asyncio.create_task(loop())


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


@app.get("/brand/{name}", include_in_schema=False)
def brand_file(name: str) -> FileResponse:
    """The SWAY logo images used by the login page and the header (a fixed list of two files; nothing else is served from here)."""
    if name not in BRAND_FILES or not (BRAND_DIR / name).is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(BRAND_DIR / name, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


# ---- installable app (PWA): manifest, service worker and icons. Data is never cached by the service worker; it only keeps the icons and an offline page.
PWA_DIR = Path(__file__).resolve().parents[2] / "frontend" / "pwa"
PWA_FILES = {"icon-192.png": "image/png", "icon-512.png": "image/png", "icon-maskable-512.png": "image/png", "apple-touch-icon.png": "image/png", "offline.html": "text/html"}
PWA_MANIFEST = {"name": "eSway ERP 2.0", "short_name": "eSway", "description": "Freight forwarding, customs (CHA), transport and accounts", "id": "/erp", "start_url": "/erp/login", "scope": "/", "display": "standalone", "orientation": "any",
                "background_color": "#0B1F3A", "theme_color": "#0B1F3A", "lang": "en",
                "icons": [{"src": "/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"}, {"src": "/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                          {"src": "/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
                "shortcuts": [{"name": "CHA jobs", "url": "/erp#svc-cha"}, {"name": "Dashboard", "url": "/erp"}]}
PWA_SW = """const V = "esway-pwa-v1";
const SHELL = ["/pwa/offline.html", "/pwa/icon-192.png", "/pwa/icon-512.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const r = e.request; if (r.method !== "GET") return;
  const u = new URL(r.url); if (u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (r.mode === "navigate") { e.respondWith(fetch(r).catch(() => caches.match("/pwa/offline.html"))); return; }
  if (u.pathname.startsWith("/pwa/")) e.respondWith(caches.match(r).then(m => m || fetch(r)));
});
"""


@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest():
    return JSONResponse(PWA_MANIFEST, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
def pwa_service_worker():
    return Response(PWA_SW, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/pwa/{name}", include_in_schema=False)
def pwa_file(name: str) -> FileResponse:
    if name not in PWA_FILES or not (PWA_DIR / name).is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(PWA_DIR / name, media_type=PWA_FILES[name], headers={"Cache-Control": "public, max-age=3600"})


@app.get("/", include_in_schema=False)
def landing() -> HTMLResponse:
    return HTMLResponse(_landing(_allowed), headers={"Cache-Control": "no-store"})


for _p in _allowed:
    app.add_api_route(f"/{_p}", _ui_response, methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_p}/login", _ui_response, methods=["GET"], include_in_schema=False)
if not PUBLIC_PORTALS:
    app.add_api_route("/prototype", _ui_response, methods=["GET"], include_in_schema=False)
