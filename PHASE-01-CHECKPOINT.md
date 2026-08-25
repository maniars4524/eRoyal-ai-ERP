# ERP Implementation Checkpoint

## Phase
01 - Authentication + Tenant Foundation

## Status
READY_FOR_NEXT_PHASE

## Implemented
- Environment-backed database and JWT configuration
- PostgreSQL SQLAlchemy engine with pooling and bounded connection timeout
- Secure scrypt password hashing and signed session tokens
- Users, organizations, roles, permissions, user-role links, role-permission links, shipments, and audit logs
- Alembic configuration and additive foundation migration `0001_foundation`
- Real `/api/auth/login`, `/api/auth/me`, and `/api/auth/logout` endpoints
- Authenticated tenant-scoped shipment endpoint
- Super Admin organization list/get/create/update endpoints
- Organization creation transaction creates the ERP Admin and assigns foundation permissions
- Existing prototype login calls `/api/auth/login`
- Existing prototype logout calls `/api/auth/logout` and clears the session token
- Dashboard summary no longer presents fake metric values
- Health endpoint reports `degraded` when PostgreSQL is unavailable
- CORS origins are environment-configured

## Tests
- Python compilation: PASS
- Focused pytest suite: PASS (4 tests)
- Frontend/prototype diagnostics: PASS
- Existing prototype rendering: previously PASS
- PostgreSQL migration: PASS (`0001_foundation` applied cleanly to a fresh database)
- Real database authentication: PASS (`/api/health` reports `database: healthy`; seeded accounts created)
- Tenant isolation integration tests: NOT VERIFIED

## Blocker
Resolved 2026-08-25. Docker Desktop previously could not start its daemon because WSL was not installed; the user installed WSL and Docker Desktop's daemon now starts and responds normally. A separate, pre-existing config gap was found and fixed while unblocking this: `backend/app/config.py` read `DATABASE_URL`/`DB_*` purely from process environment variables and never loaded the repo's `.env` file, so the FastAPI app and Alembic silently fell back to default credentials that didn't match the `docker-compose.yml`-provisioned Postgres password. Fixed by calling `python-dotenv`'s `load_dotenv()` against the repo-root `.env` in `app/config.py` (added `python-dotenv` to `backend/requirements.txt`), and by aligning the placeholder `DATABASE_URL` password in `.env`/`.env.example` with `DB_PASSWORD`.

## Next Safe Action
PostgreSQL, migration, seed, and auth are verified locally. Continue with:
- Tenant-isolation integration tests (create Tenant A/B, verify cross-tenant access fails server-side, per `ERP_PROMPT.md` §34).
- Wire the existing organization list/create UI controls to the real organization API.
- Initialize git version control for this repo and push to GitHub — the repo currently has zero commits and no remote configured, so nothing from this build exists on GitHub yet.

Local reference commands (from `backend`, once Postgres is already running):

```powershell
.\\.venv\\Scripts\\python.exe -m alembic upgrade head
$env:SUPER_ADMIN_PASSWORD = 'use-a-local-password-of-12-or-more-characters'
.\\.venv\\Scripts\\python.exe -m app.seed
.\\.venv\\Scripts\\python.exe -m pytest -q
```

## Local Login Contract

- Browser URL for both roles: `http://127.0.0.1:5173`
- Super Admin: `Admin` / `admin`
- ERP User: `mani` / `mani`
- ERP User company: `NRS FREIGHT`
- Super Admin platform company: `eRoyal`
- FastAPI remains internal at port 8000 through the Vite proxy.

After those pass, run tenant-isolation integration tests and connect the existing organization list/create controls to the organization API.
