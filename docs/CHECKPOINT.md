# Checkpoint (paused 2026-10-13, Quotations done, Bookings backend done)

Resume by reading this file, `docs/ui-audit.md`, `docs/portals.md`, `docs/deployment.md` and `git log`.

## Starting the environment
1. Start Docker Desktop, then `docker compose up -d postgres` (container `esway-postgres`; databases `esway_erp` dev, `esway_erp_demo` public, `esway_erp_qa`, `esway_erp_test`). Local secrets are in gitignored `.env`, `.env.demo`, `.env.qa`, `.env.test`.
2. Everything at once: double-click `START-PUBLIC.cmd` (also installed in the Windows Startup folder as `eSway-public.cmd`, so it runs at every login). It runs `scripts/start-private.ps1` (private app on http://127.0.0.1:8000, database `esway_erp`) and `scripts/start-public.ps1` (public app on :8001 with `ENV_FILE=.env.demo PUBLIC_PORTALS=demo,erp,developer` plus a Cloudflare quick tunnel; the public URL is written to `PUBLIC_URL.txt` and changes every time the tunnel restarts). Logs are in `logs/`. The PC must stay on and awake for the public link to work.
3. Manual private start: `cd backend && .venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m app.seed && .venv/Scripts/python -m uvicorn app.main:app --port 8000`. Do not `export` .env values in the shell (python-dotenv will not override them).
4. Tests: `cd backend && .venv/Scripts/python -m pytest -q` (75 tests). UI tests (headless jsdom against the real API): start a server with `ENV_FILE=.env.qa` on port 8010, then `cd e2e && npm install && npm run test:masters` (masters, more, users, charges, billing, org, dashboard) and `node login.test.js` against :8000.

## Local logins (this PC only)
Private app http://127.0.0.1:8000: ERP `admin`/`admin` with license RTS0004; developer `developer`/`developer`; demo `demo`/`demo`. Public stack: ERP `erp`/`erp` (license RTS0003), demo `demo`/`demo`, developer user `developer` with a strong password kept in `.env.demo` (SUPER_ADMIN_PASSWORD). These short passwords are for testing only; production enforces 12+ characters.

## Done and verified (tests passing)
Auth + three portals + lockout + audit; PUBLIC_PORTALS mode; tenant isolation; Customers/Organization master (roles, all form fields, contacts/addresses tables, GSTIN/PAN validation); Ports; Jobs (list, create, detail header, charges autosave, server-side Decimal totals); 14 masters; Users; Billing (invoice, purchase invoice, debit/credit notes, payments with allocation + idempotency, gap-free numbering, generation from job charges); real dashboard; **Quotations** (API + full UI: wizard form, rate-line grid fed by Charge Master/carrier organizations, draft/issue/accept/reject/convert-to-job, print view, gap-free QT numbers); **Bookings backend** (`/api/bookings`: draft, confirm = numbered BKG + linked job, cancel, release space, duplicate, summary; 8 tests). Job numbers are now allocated from a locked per-company counter and are unique per company (migration 0015).

## Bugs found and fixed this session (worth remembering)
- The main page script is an IIFE: anything called from `go()` as `window.x` must be exported explicitly (renderJobRows, renderPortRows, populateCustomerSelect, toast, openModal were not). Add exports when a hook silently does nothing.
- The role chips had two click handlers that cancelled each other.
- The Organization form hid required fields on other tabs; it now shows a banner and jumps to the tab.

## Not built yet (honest status)
- NOT deployed permanently: only a temporary tunnel from this PC. Render Blueprint (`render.yaml`) is ready; it needs the user's Render account and approval.
- Terms & Conditions, Chart of Accounts, Currency exchange rates/country mapping, Employee profile screen, Rate Master/Rate Sheets, Quotations, Bookings, Job sub-tabs (containers, BL, documents, tracking, customs), CRM (4 screens, in-memory sample data hidden), Customs/Transport/Warehouse/Service consoles, Manifests, BL Draft, Freight Certificate, Cargo Arrival Notice, Tax Invoice print, Journal and Contra vouchers (in browser memory only), Ledger, GST report, P&L, Job P&L report, MIS reports, dashboard filters.
- About 40 `wireAction(...)` buttons are still toast-only stubs (emailing, printing, approving, uploading). Replace or remove as each module is built.
- Employee PII (Aadhaar, bank account) is stored as plain JSON fields: add field-level encryption before real use. Role-based menu hiding, multi-company users, password reset by email, MFA, developer-portal admin pages (no UI to create companies), backups, CI, monitoring: NOT IMPLEMENTED.

## Next steps
1. **Bookings UI** (the API is done): `booking` list (rows, KPIs from `/api/bookings/summary`, Duplicate/Cancel/Release Space actions) and `booking-new` (export `bookWiz`, `bookWizApplyAndShowForm`, `bookGotoStep` from the main script; replace the fake finish/random number; fill selects from masters: customers, carriers, ports, branches, container/pack types; charges rows; real Save Draft/Confirm; remove BK_PARTY_ADDR dummy addresses). Then a headless test `e2e/bookings.test.js`.
2. Rate Master/Rate Sheets (re-enable "Get Rate" in the quotation form).
3. Journal/Contra vouchers + Chart of Accounts, Ledger, GST report, P&L (accounts reports).
4. Job sub-tabs and operations screens; CRM on the real API.
5. Developer portal: create/disable companies from the UI.
6. Replace remaining `wireAction` stubs; deploy permanently (Render), backups, CI, monitoring.


## Update 2026-10-14: CRM + Quotation phase
- Quotation Preparation redesigned (Sell left / Buy right, cost sheet, container rates, approval workflow, revisions, customer-safe view). Migration 0016. UI in QuoteKit v2 inside esway-erp.html. Test: e2e/quotes.test.js.
- CRM (leads, enquiries, opportunities, follow-ups, CRM bookings) persisted via /api/crm/{kind} (migration 0017, backend/app/crm_api.py); CrmKit syncs the existing in-memory screens. Customers come from Customer Master. Test: e2e/crm.test.js, backend/tests/test_crm.py.
- Backend: 88 tests pass. All e2e suites pass on QA server (ENV_FILE=.env.qa, port 8010; start with backend/.venv python -m uvicorn app.main:app --port 8010).
- Known limits: CRM-internal opportunity quotations/bookings are still the old in-memory flow (persisted as JSON, not linked to the real Quotation module); email sending not configured; Get Rate (Rate Master) disabled.
- NEXT: Organization Master enhancement (branches, branch contacts, COA/AP/AR classification, credit application/approval/control) - see user's spec in chat; then deploy (needs user approval).

## Update 2026-10-14 (later): Organization Master enhancement
- Migration 0018: org_branches, org_contacts, credit_facilities, credit_requests, credit_events, shipments.party_branch_id.
- Backend: app/org_ext_api.py (branches, branch contacts, accounting-class validation, credit workflow, requests, overview), app/credit_control.py (exposure + enforcement; hooked into shipment create, quotation convert, booking confirm, invoice post). Permissions credit.read/request/approve/override; template roles auto-sync at startup (roles.sync_template_roles).
- UI: OrgExt kit in esway-erp.html (tabs 01-05 + legacy tabs). Tests: backend/tests/test_org_credit.py (12), e2e/orgext.test.js.
- Quotation: Taxable Yes/No + Tax % are visible columns; preview has With Tax / Without Tax.
- QUEUED (user specs pasted in chat): Charge/SAC/GST catalogue import (47 charges, inactive until verified); Job stage + closure/reopen + right-side Job Activity History.
