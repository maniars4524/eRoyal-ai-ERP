import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / os.getenv("ENV_FILE", ".env"))


DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "esway_erp")
DB_USER = os.getenv("DB_USER", "esway")
DB_PASSWORD = os.getenv("DB_PASSWORD", "esway")
APP_ENV = os.getenv("APP_ENV", "development").lower()
# Public demo server: only the demo portal is reachable; no lockout on the shared (public) demo account.
_public = {p.strip() for p in os.getenv("PUBLIC_PORTALS", "").split(",") if p.strip()}
if os.getenv("DEMO_ONLY", "").strip().lower() in ("1", "true", "yes"):
    _public.add("demo")
# Portals reachable on a public server (e.g. "demo,erp"). Empty = a normal server where every portal is available.
PUBLIC_PORTALS = _public
# Quotation approval: the person who prepared a quotation may not approve it (not enforced for demo companies).
QUOTE_SOD = os.getenv("QUOTE_SOD", "true").strip().lower() not in ("0", "false", "no")
QUOTE_SOD_ADMIN_EXEMPT = os.getenv("QUOTE_SOD_ADMIN_EXEMPT", "true").strip().lower() not in ("0", "false", "no")        # a company Admin may approve a quotation they prepared
# One active sign-in per user: a second sign-in must confirm, and then ends the first one.
ACCESS_URL = os.getenv("ERP_ACCESS_URL", "").strip().rstrip("/")          # the permanent address handed to companies (printed on the license sheet), e.g. http://desktop-touf7i0:8000
ACCESS_NOTE = os.getenv("ERP_ACCESS_NOTE", "").strip()
SINGLE_SESSION = os.getenv("SINGLE_SESSION", "false").strip().lower() in ("1", "true", "yes")
SESSION_ACTIVE_MINUTES = int(os.getenv("SESSION_ACTIVE_MINUTES", "10"))
DEMO_ONLY = PUBLIC_PORTALS == {"demo"}
IS_PRODUCTION = APP_ENV == "production"


def _normalize_database_url(url: str) -> str:
    # Hosting providers (Render, Railway, Heroku) hand out postgres:// or postgresql:// URLs.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
))
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-local-secret")
if IS_PRODUCTION and (JWT_SECRET == "change-this-local-secret" or len(JWT_SECRET) < 32):
    raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters when APP_ENV=production")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
CORS_ORIGINS = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
).split(",") if origin.strip()]

# Basis calculation: air volumetric divisor (L x W x H cm x packages / divisor = kg). Airlines and tariffs differ: set AIR_VOLUMETRIC_DIVISOR (e.g. 5000) to change it.
AIR_VOLUMETRIC_DIVISOR = int(os.getenv("AIR_VOLUMETRIC_DIVISOR", "6000") or 6000)
