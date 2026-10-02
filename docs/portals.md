# Portals and access (eSway ERP 2.0)

One authentication system (FastAPI, scrypt password hashes, signed tokens, PostgreSQL) serves three portals.
The portal an account may use is **derived on the server from the account** and never trusted from the client.

| Portal | URL | Who | Rule |
|---|---|---|---|
| Developer / System Admin | `/developer/login` | Platform Super Admin | `is_super_admin = true`, no tenant data access |
| ERP | `/erp/login` | Business users of a real company | Organization with `is_demo = false`; license number required |
| Demo | `/demo/login` | Evaluators | Organization with `is_demo = true` (isolated tenant) |

- Logging in to the wrong portal returns **403** with a message and writes a `LOGIN_WRONG_PORTAL` audit row.
- Tokens carry a `portal` claim; a token is rejected if it no longer matches the account's portal.
- Demo users cannot call any `*.delete` permission or create/update organizations (`DEMO_BLOCKED_PERMISSIONS` in `backend/app/auth.py`).
- Lockout: 5 failed sign-ins lock the account for 15 minutes (HTTP 429). Audit actions: `LOGIN`, `LOGIN_FAILED`, `ACCOUNT_LOCKED`, `LOGIN_BLOCKED_LOCKED`, `LOGIN_WRONG_PORTAL`, `LOGOUT`.
- The demo tenant is created by `python -m app.seed` only when `DEMO_PORTAL_PASSWORD` is set (never committed).

## Status
| Item | State |
|---|---|
| Portal-restricted login, lockout, demo restrictions, tenant isolation (`backend/tests`, 16 tests) | IMPLEMENTED - VERIFIED (pytest, plus live curl against the running server) |
| Portal login screens and wrong-portal session rejection in the browser | IMPLEMENTED - NOT YET VERIFIED in a browser |
| Role-based menu hiding in the UI | NOT IMPLEMENTED (backend enforces permissions; the menu still shows all items) |
| Multiple companies/branches per user, company selector | NOT IMPLEMENTED (one organization per user today) |
| Password reset, email verification, MFA | NOT IMPLEMENTED |
| Developer portal pages (system health, logs, feature flags, sessions) | NOT IMPLEMENTED (Organizations screen exists) |
| Refresh tokens / session revocation | NOT IMPLEMENTED (8 hour access token) |
