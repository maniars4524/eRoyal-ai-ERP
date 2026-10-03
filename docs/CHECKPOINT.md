# Checkpoint (paused 2026-10-03, after the Organization master)

Resume by reading this file, `docs/ui-audit.md`, `docs/portals.md`, `docs/deployment.md` and `git log`.

## Starting the environment
1. Start Docker Desktop, then `docker compose up -d postgres` (container `esway-postgres`; databases `esway_erp` dev, `esway_erp_demo` public, `esway_erp_qa`, `esway_erp_test`). Local secrets are in gitignored `.env`, `.env.demo`, `.env.qa`, `.env.test`.
2. Private app: `cd backend && .venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m app.seed && .venv/Scripts/python -m uvicorn app.main:app --port 8000`. Do not `export` .env values in the shell (python-dotenv will not override them).
3. Public stack (demo + ERP + developer pages on its own database): `tools/start-public.ps1` (starts the app on :8001 with `ENV_FILE=.env.demo PUBLIC_PORTALS=demo,erp,developer` and a Cloudflare quick tunnel; the current public URL is written to `PUBLIC_URL.txt`). A scheduled task/startup entry runs it at login. The tunnel URL changes every time the tunnel restarts.
4. Tests: `cd backend && .venv/Scripts/python -m pytest -q` (56 tests). UI tests (headless jsdom against the real API): start a server with `ENV_FILE=.env.qa` on port 8010, then `cd e2e && npm install && npm run test:masters` (masters, more, users, charges, billing, org, dashboard) and `node login.test.js` against :8000.

## Local logins (this PC only)
Private app http://127.0.0.1:8000: ERP `admin`/`admin` with license RTS0004; developer `developer`/`developer`; demo `demo`/`demo`. Public stack: ERP `erp`/`erp` (license RTS0003), demo `demo`/`demo`, developer user `developer` with a strong password kept in `.env.demo` (SUPER_ADMIN_PASSWORD). These short passwords are for testing only; production enforces 12+ characters.

## Done and verified (tests passing)
Auth + three portals + lockout + audit; PUBLIC_PORTALS mode; tenant isolation; Customers/Organization master (roles shipper/consignee/carrier/agent/CHA/transporter/AR/AP, all form fields, contacts/addresses/registrations/staff tables, GSTIN/PAN validation, role-filtered dropdowns); Ports; Jobs (list, create, detail header, charges autosave, server-side Decimal totals); 14 masters; Users (roles, reset password); Billing (invoice, purchase invoice, debit/credit notes, payments with allocation + idempotency, gap-free numbering, generation from job charges); real dashboard (`/api/dashboard/summary`).

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
1. Quotations + Bookings, Rate Master/Rate Sheets (feeds quotations).
2. Journal/Contra vouchers + Chart of Accounts, Ledger, GST report, P&L (accounts reports).
3. Job sub-tabs and operations screens; CRM on the real API.
4. Developer portal: create/disable companies from the UI.
5. Replace remaining stubs; deploy permanently (Render), backups, CI, monitoring.
