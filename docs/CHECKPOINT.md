# Checkpoint (paused 2026-10-02)

Resume by reading this file, `docs/ui-audit.md`, `docs/portals.md`, `docs/deployment.md` and `git log`.

## How to start the environment tomorrow
1. Start Docker Desktop, then `docker compose up -d postgres` (container `esway-postgres`, volume `eroyal-ai-erp_esway-postgres-data`; the OLD volume `eroyal-ai-erp_eroyal-postgres-data` is untouched and unused).
2. Local secrets are in gitignored `.env`, `.env.demo`, `.env.qa`, `.env.test` (DB role `esway`; databases `esway_erp`, `esway_erp_demo`, `esway_erp_qa`, `esway_erp_test`). If lost, recreate from `.env.example` and set the role password with `docker exec esway-postgres psql -U esway -d postgres -c "ALTER USER esway PASSWORD '...'"`.
3. `cd backend && .venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m app.seed` (per ENV_FILE), then `.venv/Scripts/python -m uvicorn app.main:app --port 8000`. Open http://127.0.0.1:8000 (portals: /developer/login, /erp/login, /demo/login).
4. Do not `export` .env values in the shell: python-dotenv will not override them.
5. Tests: `cd backend && .venv/Scripts/python -m pytest -q` (28 tests). UI tests: start the backend with `ENV_FILE=.env.qa` on port 8010, then `cd e2e && npm install && npm run test:masters` (3 files, headless jsdom against the real API).

## Done and verified
Auth + 3 portals + lockout + audit; tenant isolation; Customer, Port, Shipments/Jobs; Job Detail header from real data; generic masters API (`/api/masters/{type}`) with UI for Country, Currency, Unit, Pack, Incoterm, HSN/SAC, Container Type, Department, Designation, Branch, Employee, Route, Charge (modal); Users API + User Master UI (roles: Admin/Operations/Accounts/Viewer, password reset); production Docker image verified locally.

## Known gaps / honest status
- NOT deployed anywhere. No public URL, HTTPS, backups, CI, monitoring. Render Blueprint (`render.yaml`) is ready; deploying needs the user's Render account and explicit approval.
- Not built: Terms & Conditions, Chart of Accounts, Currency exchange rates/country mapping, Organization detail form wiring, Rate Master/Rate Sheets, Quotations, Bookings, Job sub-tabs (charges, containers, BL, documents, tracking, vouchers), Invoices, Debit/Credit notes, Purchase invoices, Vendor bills, Payments, Journal/Contra vouchers, ledgers, GST, Customs/Transport/Warehouse/Manifests, CRM, Reports, Dashboard numbers, Employee grid view, Loan/advance.
- ~46 `wireAction(...)` buttons in `esway-erp.html` are still toast-only stubs (emailing, printing, approving, uploading, etc.). They must be replaced with real actions or removed as each module is built.
- Employee PII (Aadhaar, bank account) is stored as plain JSON fields: add field-level encryption before real use.
- Role-based menu hiding, multi-company users, password reset by email, MFA, developer-portal admin pages: NOT IMPLEMENTED.
- Browser check by a human has not happened; only headless jsdom tests.

## Next steps (recommended order)
1. Job charges (buy/sell lines, Decimal totals) saved on the server, fed by Charge Master; Job P&L from them.
2. Document framework with server-side numbering and totals: Quotation, Booking, Invoice, Debit Note, Purchase Invoice, Vendor Bill, Payment, vouchers.
3. Rate Master/Rate Sheets, Terms & Conditions, COA, currency rates.
4. Job sub-tabs (containers, BL, documents, tracking, customs), operations screens, CRM.
5. Dashboard and reports on real data; replace remaining `wireAction` stubs.
6. Deploy to Render (ask the user first), backups, CI.
