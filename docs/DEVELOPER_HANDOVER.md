# eSway ERP 2.0 - Developer Handover (layer by layer)

Written for: the developer or team taking over the whole application (code, database, tools, deployment).
Date: 2026-11-07. Repository branch `main`. Live database schema is at Alembic head `0067_notification_reads`.

Read this first, then `docs/CHECKPOINT.md` (a dated log of every batch of work, newest at the bottom) and `docs/RUN-AND-DEPLOY-GUIDE.md`.

---------------------------------------------------------------------
## 0. What the product is

A multi-tenant freight forwarding ERP: Sea / Air / Domestic freight jobs, CHA (customs clearance), NVOCC / container operations, transport / fleet, CRM, quotations, bookings, charges and costing, invoicing and accounts (GL), documents (BL, HAWB, CAN and others), notifications, licences and roles.

Each customer company is an **organization (tenant)** with its own licence number (`SWF00001+`). Three portals share one login system: Developer / System Admin (`/developer/login`), ERP (`/erp/login`), Demo (`/demo/login`). See `docs/portals.md`.

---------------------------------------------------------------------
## 1. The layers

| # | Layer | Where | Technology |
|---|---|---|---|
| 1 | Hardware / host | the owner's desktop PC (host name `desktop-touf7i0`) | Windows 11 |
| 2 | Network / access | LAN, Tailscale (permanent URL `desktop-touf7i0:8000`), optional Cloudflare tunnel (`tools/cloudflared.exe`) | Tailscale, cloudflared |
| 3 | Process supervision | `scripts/keep-alive.ps1`, Windows scheduled task `eSwayERP-KeepAlive` (every minute) | PowerShell |
| 4 | Database | Docker container `esway-postgres` (image `postgres:16-alpine`, volume `esway-postgres-data`, port 5432) | PostgreSQL 16 |
| 5 | Schema migrations | `backend/migrations/versions` (68 files) | Alembic |
| 6 | Backend (API) | `backend/app` (75 Python modules, 44 routers in `main.py`) | FastAPI, SQLAlchemy 2, Pydantic 2, psycopg 3 |
| 7 | Front end | `esway-erp.html` (single file, about 2.7 MB, served by the backend from disk) | plain HTML/CSS/JS, no build step |
| 8 | Secondary front end | `frontend/` (Vite + React stub, 3 small files; **not** the real UI) | Vite |
| 9 | Tests | `backend/tests` (pytest, 457 tests), `e2e/*.test.js` (121 browser/JSDOM scripts) | pytest, puppeteer, jsdom |
| 10 | Backup / deploy | `scripts/backup-db.ps1`, `backups/`, `Dockerfile`, `render.yaml` (Render blueprint, not live) | pg_dump, Docker |

---------------------------------------------------------------------
## 2. Layer detail

### 2.1 Host, network, supervision
- Live URL on the LAN / Tailscale: `http://desktop-touf7i0:8000`. Only port 8000 is the live app. Port 8010 is the QA server (see 2.6).
- `scripts/keep-alive.ps1` checks `/api/health` every minute; if it fails it starts Docker and the database, removes stale server copies, starts ONE uvicorn on port 8000 and makes sure Tailscale is up. **It stops every uvicorn process, including the QA server on 8010.**
- Other scripts: `start-postgres.ps1`, `start-private.ps1`, `start-public.ps1`, `INSTALL-KEEPALIVE.cmd` (installs the scheduled task), `ALLOW-NETWORK.cmd` (firewall), `RUN-LOCAL.cmd`.
- Restart live by hand: stop uvicorn on 8000, then `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/keep-alive.ps1` (the script keeps running; run it in the background).
- Hosting plan on record: Render instead of SmarterASP (`render.yaml`, `docs/deployment.md`). Not live yet.

### 2.2 Database (PostgreSQL)
- Databases in the same container: `esway_erp` (LIVE, real data), `esway_erp_test` (pytest), `esway_erp_qa2` (browser tests), plus older `esway_erp_demo` / qa copies.
- Never reset or drop the live database. Deletes are done only through the application front end (project rule).
- Backups: `scripts/backup-db.ps1` writes `backups/esway_erp_<date>.dump`. `backups/` is NOT in git (ignored). Take a dump before every migration that changes data or structure.
- Restore: `docker exec -i esway-postgres pg_restore -U esway -d esway_erp --clean < backups/<file>.dump` (create the database first if it does not exist).
- Every business table carries `organization_id`; all queries must filter by it (tenant isolation is tested).

### 2.3 Migrations (Alembic)
- Config `backend/alembic.ini`; run from `backend/`: `.venv/Scripts/python -m alembic upgrade head`.
- Naming `NNNN_description.py`; always add a new file, never edit an applied one.
- Order of work for a backend change that touches the schema: write the migration, run it on the test DB, take a live dump, apply to live, restart live.
- Latest: `0061_job_routing` ... `0067_notification_reads` (routing, output docs, internal messages, notification settings, planned containers, charge DR/CR, notification read marks).

### 2.4 Backend (`backend/app`)
Entry: `main.py` mounts routers, serves the HTML at `/erp`, `/demo`, `/developer`, adds `/api/health`. Config in `config.py` (reads the `.env` file chosen by the `ENV_FILE` variable).

Foundation modules
- `db.py` engine and session, `models.py` all tables (one file), `schemas*.py` request/response models.
- `auth.py`, `auth_api.py`, `security.py` (scrypt, signed tokens, lockout), `roles.py`, `access.py`, `access_api.py` (permissions, role scope, `shipment_clause` / `record_clause` filters), `users_api.py`.
- `licensing.py`, `license_mgmt*.py` (licence numbers, console), `sequences*.py` (number-on-save numbering).
- `audit_api.py` audit trail.

Business modules (router file -> what it does)
- Masters: `masters_api`, `std_masters`, `reference_masters`, `customers_api`, `org_ext_api`, `org_links`, `ports_api` / `ports_import`, `network_api`, `charge_catalogue`, `charge_rules`, `currency_ref`, `coa` / `coa_api`.
- Sales: `crm_api`, `quotations_api` + `quote_calc`, `bookings_api`.
- Jobs: `shipments_api`, `job_details_api` (parties, cargo, containers, BL/AWB), `jobs_lifecycle_api` + `job_lifecycle` + `job_locks` (status, cancel, close), `routing_api` (ETD/ETA legs), `hbl_clauses`, `air_weight`, `cargo_api` + `cargo_engine` (dimensions, consolidation), `basis_api` + `basis_calc` (charge basis).
- Charges and billing: `job_charges_api` (buy/sell, tax, DR/CR, Debit/Credit Note), `documents_api` (invoices, notes, vouchers), `billing`, `payments_api`, `credit_control`, `accounts_api`, `gl` / `gl_api`.
- CHA / customs: `cha_api`, `cha_checklist`, `cha_icegate`, `cha_spec`, `customs_api` (Shipping Bills / Bills of Entry per CHA job).
- NVOCC / containers / transport: `containers_api`, `container_ops_api`, `container_files_api`, `fleet_api`.
- Output documents: `outdocs_api` (catalogue by mode and direction, sanitised HTML, versions, mail via `/api/jobs/{job}/send-document`).
- Cross-cutting: `notifications_api` (live providers + settings + read marks + calendar), `dashboard_api`, `mis_api`, `search_api`, `universal_search`.
- Reference data in `backend/data` (ports, airlines, countries, currencies, SAC codes). One-off tools: `backend/scripts_*.py`.

Conventions
- Permissions are strings such as `shipment.read`, `billing.create`; routes use `permission_required(...)`.
- Money is `Decimal`; tax types `Taxable | RCM | Non-Taxable`.
- Dependencies (`backend/requirements.txt`): fastapi, uvicorn, sqlalchemy, psycopg, pydantic-settings, python-dotenv, alembic, pytest, httpx, segno, pycountry.
- Mail needs `SMTP_*` settings in the server `.env` (not configured yet).

### 2.5 Front end (`esway-erp.html`)
- One file, served live from disk: **any edit is live immediately**. After every edit run the syntax check: `python scratch/fix_csv2.py; node --check scratch/allscripts.js`.
- Structure: HTML screens (each `go("screen-id")`), then many IIFE "kits" appended before `</body>` (Customs kit, Job detail kit, Routing kit, Output Docs kit with `DocKit` and `BLDocKit`, Notification centre, Saved-state kit, Wizards, Lifecycle kit, and others).
- Patterns: `MK.util.api` for calls, `MK.onScreen` hook per screen, `openModal`, `SCREEN_META` and nav tree arrays for menus, `window.__fetchCacheClear()` for the 15 s customer cache, `window.markSaved` for the green "Saved" button state.
- The Vite app in `frontend/` is an unused stub; do not build features there unless you decide to migrate.
- Known risk: a 2.7 MB single file. Splitting into modules is an open optional task.

### 2.6 Tests
- Backend: `cd backend; .venv/Scripts/python -m pytest -q` (uses `.env.test`, database `esway_erp_test`). Last full run: **457 passed**.
- Browser (real Chrome via puppeteer) in `e2e/`: start the QA server on 8010 with `ENV_FILE=.env.qa2`, then `node e2e/<name>.test.js`. 88 of the 121 scripts are old JSDOM tests, many stale (see section 5).
- Always use the QA database for e2e and inventory/cargo work, never live.

### 2.7 Deployment and release routine used so far
1. Implement, test (pytest + the matching e2e script).
2. `git commit`, `git push`.
3. If backend or schema changed: dump live, `alembic upgrade head`, restart live, restart QA server.
4. Add a section to `docs/CHECKPOINT.md`.

---------------------------------------------------------------------
## 3. Environment and secrets (names only; values are NOT in git)

Files: `.env` (live), `.env.test`, `.env.qa`, `.env.qa2`, `.env.demo`. Template: `.env.example`. Keys: `DATABASE_URL`, `DB_*`, `POSTGRES_*`, `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `SUPER_ADMIN_*`, `DEMO_*`, `DEMO_PORTAL_*`, `APP_ENV`, `ERP_ACCESS_URL`, `ERP_ACCESS_NOTE`, and (to add) `SMTP_*`.
Hand these over through a private channel, never through GitHub.

## 4. What is NOT in GitHub (hand over separately)
- The live database dump (fresh `pg_dump` of `esway_erp`) and the `backups/` folder.
- All `.env*` files.
- `tools/cloudflared.exe`, logs, `scratch/` helper scripts (patch scripts; optional).
- Claude's project memory folder (`C:\Users\hp\.claude\projects\e--eRoyal-ai-ERP\memory`) with the working rules.
- Any uploaded files stored outside the database: **not verified, check before the move**.

## 5. Project rules from the owner (keep following them)
- Never seed starter / sample master data; offer import and pick-lists instead.
- Never remove or reset the live DB without the owner's OK; deletes only via the front end.
- Confirm before deploys, destructive actions and anything with secrets; routine development needs no confirmation.
- Numbers (jobs, documents) are issued on save; HBL terms come from Company letterhead > Printed wording > "Bill of Lading terms" or the built-in clauses.
- No password length rule. Licence numbers `SWF00001+`.

## 6. Current status
Done and verified this phase: notification centre and settings, routing, Output Docs (sailing confirmation, shipment summary, pre-alert, HBL, HAWB in the IATA format, CAN with taxable / non-taxable charges, MAWB/MBL and others), import-job BL rules, DR/CR with Debit/Credit Note, job cancel / reinstate, NVOCC masters, shipment templates, access scope on dashboard and MIS, Customs tab listing every CHA job of a booking with filed / not filed.

Test state: backend 457 passed. Browser sweep: failures left are old JSDOM tests and stale menu assertions; **still to look at**: `universalsearch`, `newtab`, `mobile` (manifest check), `allreports` and `invaudit` (timeouts), `invmasters` (expects the old Inventory menu).

## 7. Open work (backlog)
Needs the owner: HBL terms and conditions text or PDF; real SMTP settings; paper-print check of HAWB and BL on the printer and pre-printed stationery; job numbers 8/9/10 renumber decision (left as is); Demo portal removal (left as is); linking shipment templates to quotation and enquiry (left as is).

Development backlog: branch / placement / assignment screens; customer approval workflow; role dashboards and CEO dashboard with selector; licence and company onboarding; job cost "% of other charges" chooser; missing weight / volume message for By Gross Wt / Volume; company logo at licence creation; search created-by filter and "View all"; Save & New on organisation / port / Charge Master; rows-per-page on other lists; inventory stores and dashboards / reports / accounting, cost-history and audit tabs, capacity and idle alerts; cargo dimension phases (job-costing display and override audit, verification, admin config, real consolidation master / house); static-screen honesty walk (`docs/HONESTY-AUDIT.md`), CSN is front-end only; refresh old JSDOM tests and date-dependent tests; optional Brotli, staging, CI, page splitting; IATA logo on the HAWB conditions page; visual check of MAWB / MBL / stuffing / destuffing / POD / air manifest layouts; quotation "Saved" mark not confirmed after a repaint.

## 8. First day for the new developer
1. Clone the repo; install Git, Python 3.11+, Node 20+, Docker Desktop, Chrome.
2. `docker compose up -d postgres`, create the `.env` files from `.env.example`, restore the dump.
3. `cd backend; python -m venv .venv; .venv/Scripts/pip install -r requirements.txt; .venv/Scripts/python -m alembic upgrade head` (should report head `0067`).
4. `.venv/Scripts/python -m uvicorn app.main:app --port 8000` and open `http://127.0.0.1:8000/erp`.
5. `.venv/Scripts/python -m pytest -q` (expect 457 passed), then `cd e2e; npm install`.
6. Read `docs/CHECKPOINT.md` from the bottom up to see what changed most recently.
