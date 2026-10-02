# Deployment

The app is one container: FastAPI serves the API and the UI, backed by PostgreSQL.

- Local: `docker compose up -d postgres`, create `.env` from `.env.example`, `alembic upgrade head`, `python -m app.seed`, `uvicorn app.main:app`.
- Production image: `Dockerfile` runs `alembic upgrade head`, `python -m app.seed`, then uvicorn. `APP_ENV=production` forces a strong `JWT_SECRET` and Super Admin password and skips demo seeding unless `DEMO_PORTAL_PASSWORD` is set.
- Render: `render.yaml` defines the web service and a managed Postgres. Set `SUPER_ADMIN_EMAIL`, `SUPER_ADMIN_USERNAME`, `SUPER_ADMIN_PASSWORD` in the dashboard (never in git).
- Hosting note: SmarterASP.NET cannot run this stack (no Python, no PostgreSQL).

## Status
| Item | State |
|---|---|
| Docker image builds and runs in production mode (migrations, seed, health, UI, headers) | IMPLEMENTED - VERIFIED locally |
| Public URL / HTTPS / managed database | NOT IMPLEMENTED (needs a hosting account; not deployed) |
| Backups and restore test | NOT IMPLEMENTED |
| CI pipeline | NOT IMPLEMENTED |
