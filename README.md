# eSway ERP 2.0

Freight-forwarding ERP: FastAPI + PostgreSQL backend, single-page UI (`esway-erp.html`) served by the backend.

Modules with a real backend today: authentication and three portals, organizations (tenants), Customer Master, Port Master, Shipments/Jobs. Everything else in the UI is still being migrated from the prototype; see `docs/portals.md` and `docs/deployment.md` for honest status tables.

## Run locally
```
docker compose up -d postgres
cp .env.example .env            # then set real values
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m app.seed
.venv/Scripts/python -m uvicorn app.main:app --port 8000     # open http://127.0.0.1:8000
.venv/Scripts/python -m pytest -q
```
