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
DEMO_ONLY = os.getenv("DEMO_ONLY", "").strip().lower() in ("1", "true", "yes")
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
