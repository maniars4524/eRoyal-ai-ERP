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
- Tenant isolation integration tests: PASS (`backend/tests/test_tenant_isolation.py`; cross-tenant read/update/list all return 403 or are filtered server-side)
- Three local environments (DEV/DEMO/QA), each with its own database, backend port, and frontend port: PASS — see "Local Environments" below

## Blocker
Resolved 2026-08-25. Docker Desktop previously could not start its daemon because WSL was not installed; the user installed WSL and Docker Desktop's daemon now starts and responds normally. A separate, pre-existing config gap was found and fixed while unblocking this: `backend/app/config.py` read `DATABASE_URL`/`DB_*` purely from process environment variables and never loaded the repo's `.env` file, so the FastAPI app and Alembic silently fell back to default credentials that didn't match the `docker-compose.yml`-provisioned Postgres password. Fixed by calling `python-dotenv`'s `load_dotenv()` against the repo-root `.env` in `app/config.py` (added `python-dotenv` to `backend/requirements.txt`), and by aligning the placeholder `DATABASE_URL` password in `.env`/`.env.example` with `DB_PASSWORD`.

## Local Environments

Real internet-hosted DEV/QA/DEMO/PRODUCTION (per `ERP_PROMPT.md` §4) requires a domain
and hosting account this machine does not have, so per the user's direction these are
simulated locally instead: same Postgres server, three separate databases, three
separate backend ports, three separate frontend ports. Each is fully isolated data —
none share a database.

| Environment | URL | Role | Username | Password |
|---|---|---|---|---|
| DEV (Super Admin / software company) | http://127.0.0.1:5173 | Super Admin | `Admin` | `LocalDev-SuperAdmin-2026` |
| DEMO (sales demo tenant) | http://127.0.0.1:5174 | Super Admin | `Admin` | `DemoEnv-SuperAdmin-2026` |
| QA (testing / implementation) | http://127.0.0.1:5175 | Super Admin | `Admin` | `QaEnv-SuperAdmin-2026` |

**2026-08-25 update:** all seeded/test organizations were cleared from all three
databases at the user's request (`DELETE FROM organizations`, cascading to their
org-scoped users, roles, and shipments). The Super Admin account in each environment
was preserved (not organization-scoped). The previously-documented tenant logins
(`mani`, `demo`, `qauser`) no longer exist as a result — log in as `Admin` and use the
organization-create flow to provision a fresh organization and its ERP Admin.

Backend APIs: DEV `http://127.0.0.1:8000`, DEMO `http://127.0.0.1:8001`, QA `http://127.0.0.1:8002`.
Config files: `.env` (DEV), `.env.demo`, `.env.qa` — all gitignored (local secrets only).
To (re)start an environment's backend: `ENV_FILE=.env.demo python -m uvicorn app.main:app --port 8001` (from `backend`).
To (re)start an environment's frontend: `VITE_DEV_PORT=5174 VITE_API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev -- --port 5174` (from `frontend`).

These are local-only convenience ports for now, not a real deployment — Phases 17-19
(actual DEV/QA/DEMO hosting with a real domain) are still ahead in the roadmap.

## License Numbers

**2026-09-02:** organizations now carry a `license_number` (e.g. `RTS0001`), auto-assigned
sequentially per environment (`backend/app/licensing.py::next_license_number`). `RTS0001`
is reserved for the platform organization "eRoyal" (created by `python -m app.seed`, not
tied to any login). Every organization created afterward — via the Super Admin
organization-create flow or by seed — gets the next number. Currently: DEV has RTS0001
(eRoyal) and RTS0002 (NRS FREIGHT); DEMO and QA each have RTS0001 (eRoyal) with no client
orgs yet. Migration: `0002_license_number`.

## Remember Me

**2026-09-02:** the login screen's "Remember me" checkbox was previously present in the
HTML but not wired to anything. It now saves/restores the **username only** in
`localStorage` (`eroyal_remembered_username`). The password is intentionally never stored
client-side — storing a plaintext password in `localStorage` is a real security risk
(readable by any extension or XSS on the origin); the user confirmed this scope before
implementation.

## Next Safe Action
PostgreSQL, migration, seed, auth, and tenant isolation are verified locally across
all three environments. Continue with:
- Wire the existing organization list/create UI controls to the real organization API.
- Push this repo to GitHub (repo has an initial commit; a remote still needs to be added — see project memory / ask the user for the repo URL).

Local reference commands (from `backend`, once Postgres is already running):

```powershell
.\\.venv\\Scripts\\python.exe -m alembic upgrade head
$env:SUPER_ADMIN_PASSWORD = 'use-a-local-password-of-12-or-more-characters'
.\\.venv\\Scripts\\python.exe -m app.seed
.\\.venv\\Scripts\\python.exe -m pytest -q
```

## Local Login Contract

**2026-09-02 update:** at the user's request, the DEV Super Admin's own username/password
were changed from `Admin`/`LocalDev-SuperAdmin-2026` to `mani`/`mani`, and the NRS FREIGHT
organization was restored (its ERP Admin login is `nrs_admin`/`NrsFreight-Admin-2026` —
`mani` could not be reused for it since it now belongs to the Super Admin account). The
plaintext "Demo credentials" hint that was previously shown on the login card in
`eRoyal-ai-ERP.html` has been removed.

DEV (`http://127.0.0.1:5173`):
- Super Admin (platform, company "eRoyal"): `mani` / `mani`
- ERP Admin (NRS FREIGHT): `nrs_admin` / `NrsFreight-Admin-2026`

DEMO (`http://127.0.0.1:5174`) and QA (`http://127.0.0.1:5175`) were **not** touched by
this change — their Super Admin logins are still `Admin` / `DemoEnv-SuperAdmin-2026` and
`Admin` / `QaEnv-SuperAdmin-2026` respectively, with no organizations (cleared earlier,
not yet recreated).

FastAPI remains internal (DEV: 8000, DEMO: 8001, QA: 8002) behind each environment's Vite proxy.
