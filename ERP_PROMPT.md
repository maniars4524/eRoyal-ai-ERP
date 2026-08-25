# COMMERCIAL MULTI-TENANT ERP
## MASTER IMPLEMENTATION + TESTING + DEPLOYMENT PROMPT

---

### PROJECT PURPOSE

I am a software company developing an ERP product that will be sold and provided to multiple client companies.

I already have an existing ERP HTML/React prototype running locally:

`http://127.0.0.1:5173`

The existing prototype contains substantial UI, navigation, layouts, forms, tables, dashboards and ERP screens.

The existing UI is valuable and must be preserved.

The objective is to convert the existing prototype into a:

- Fully functional ERP
- Secure ERP
- Multi-tenant ERP
- Commercially deployable ERP
- Production-ready ERP
- Maintainable long-term software product

---

### 1. CORE RULE — DO NOT DAMAGE THE EXISTING ERP

The existing React/HTML UI is the source of truth.

**DO NOT:**

- Rebuild the UI from scratch
- Replace the existing design
- Delete existing pages
- Remove existing navigation
- Replace existing components unnecessarily
- Change the visual identity
- Replace the existing framework unnecessarily
- Replace PostgreSQL with another database
- Delete existing business functionality
- Create an unrelated ERP interface

Instead, transform the existing prototype:

EXISTING UI
    +
REAL BACKEND
    +
REAL DATABASE
    +
REAL BUSINESS LOGIC
    +
REAL AUTHENTICATION
    +
REAL PERMISSIONS
    +
REAL TENANT ISOLATION
    +
REAL WORKFLOWS
    +
REAL AUDIT
    +
REAL REPORTING
    =
FULLY FUNCTIONAL ERP

---

### 2. CURRENT PROJECT

**Current local application:**
`http://127.0.0.1:5173`

**Current audit status:**

* **Frontend:** React / HTML prototype
* **Backend:** Partial FastAPI scaffold
* **Database:** PostgreSQL configured
* **API:** Health, Dashboard Summary, Organization CRUD
* **Business Logic:** Minimal
* **Authentication:** Hardcoded
* **Permissions:** Not enforced
* **Workflow:** Prototype only
* **Audit:** Static prototype records
* **Migrations:** Missing
* **Tenant Isolation:** Missing
* **Tests:** Runtime health check only

The first priority is to fix the foundation.

---

### 3. COMMERCIAL ERP ARCHITECTURE

This ERP is a commercial software product.

**Use:**
ONE CODEBASE
+
MULTIPLE ENVIRONMENTS
+
MULTIPLE CLIENT TENANTS
+
STRICT TENANT ISOLATION
+
CENTRALIZED PLATFORM MANAGEMENT

Do **NOT** create a separate codebase for every customer.

---

### 4. ENVIRONMENT ARCHITECTURE

**Required environments:**
- DEV
- QA
- DEMO
- PRODUCTION

**Architecture:**

                    ERP PRODUCT
                         |
        +----------------+----------------+
        |                |                |
       DEV              QA              DEMO
        |                |                |
   Developers       Internal QA       Sales Demo
        |                |                |
        +----------------+----------------+
                         |
                    PRODUCTION
                         |
          +--------------+--------------+
          |              |              |
       Client A       Client B       Client C
        Tenant A       Tenant B       Tenant C

**Recommended URLs:**

* **DEV:** `dev-erp.<company-domain>`
* **QA:** `qa-erp.<company-domain>`
* **DEMO:** `demo-erp.<company-domain>`
* **PRODUCTION:** `erp.<company-domain>`

Do not hardcode the actual domain. Use environment configuration.

---

### 5. ENVIRONMENT ISOLATION

Each environment must have separate configuration.

**Conceptually:**
- `.env.development`
- `.env.qa`
- `.env.demo`
- `.env.production`

* Never commit production secrets.
* Never expose secrets in frontend code.
* Never use production credentials in development.
* Never use production databases for:
  - Development
  - Testing
  - AI experimentation
  - Demo
  - Migration experiments

---

### 6. DATABASE ARCHITECTURE

Use PostgreSQL.

**DO NOT** switch to SQLite simply because PostgreSQL is temporarily unavailable.

**Recommended:**
* **DEV** → DEV PostgreSQL
* **QA** → QA PostgreSQL
* **DEMO** → DEMO PostgreSQL
* **PRODUCTION** → PRODUCTION PostgreSQL

Production database must be isolated from development and testing. Do not expose PostgreSQL directly to the public internet.

---

### 7. FIRST DATABASE TASK

**Current issue:**
The database seed still requires PostgreSQL/Docker to be running.

* Do **NOT** bypass this.
* Do **NOT** create fake or in-memory database data.
* Do **NOT** switch databases.

**Inspect:**
- Docker
- `docker-compose.yml` / `compose.yaml`
- PostgreSQL service
- `DATABASE_URL`
- Database credentials
- Database host
- Database port
- Database name
- ORM configuration
- Migration configuration
- Seed scripts

Determine the actual root cause. Repair the existing PostgreSQL/Docker setup. Do not delete database volumes. Do not reset existing data without explicit approval.

---

### 8. DATABASE MIGRATIONS

Implement a proper database migration system using the existing ORM and architecture (e.g., Alembic for FastAPI + PostgreSQL).

**Required lifecycle:**

MODEL CHANGE
    ↓
MIGRATION
    ↓
DEV
    ↓
TEST
    ↓
QA
    ↓
TEST
    ↓
DEMO
    ↓
APPROVAL
    ↓
PRODUCTION

Never rely on manually creating production tables. Never perform destructive migrations automatically.

---

### 9. MULTI-TENANT ARCHITECTURE

Treat each ERP customer as a Tenant / Organization.

**Example:**

TENANT A
├── Users
├── Roles
├── Permissions
├── Customers
├── Suppliers
├── Items
├── Sales
├── Purchase
├── Inventory
└── Accounting

TENANT B
├── Users
├── Roles
├── Permissions
├── Customers
├── Suppliers
├── Items
├── Sales
├── Purchase
├── Inventory
└── Accounting

Tenant A must **NEVER** access Tenant B data.

---

### 10. TENANT ISOLATION

Tenant isolation must be enforced by the backend.

Never trust frontend-supplied `tenant_id` or `organization_id` as the authorization source.

**Required architecture:**

LOGIN
 ↓
AUTHENTICATED USER
 ↓
USER TENANT
 ↓
TENANT CONTEXT
 ↓
AUTHORIZATION
 ↓
BUSINESS SERVICE
 ↓
DATABASE

Every tenant-owned database record must belong to the correct tenant. Test cross-tenant access explicitly.

---

### 11. SUPER ADMIN

The software company has a platform-level Super Admin.

**Super Admin can:**
- Login / Logout
- Create, view, edit, activate, and deactivate tenants
- Create initial ERP Admin
- Manage platform configuration
- View platform audit
- Manage client onboarding

Super Admin is **NOT** an ordinary ERP user. Super Admin permissions must be separate from tenant permissions.

---

### 12. ERP ADMIN

Each client tenant has an ERP Admin.

ERP Admin belongs to one tenant unless explicitly designed otherwise.

**ERP Admin can:**
- Manage company profile, ERP users, roles, permissions
- Manage branches, warehouses, masters, ERP configuration
- Access permitted ERP modules
- View permitted reports

ERP Admin must **NOT** automatically have access to other tenants.

---

### 13. CLIENT ONBOARDING

**Required production onboarding flow:**

SUPER ADMIN
    ↓
CREATE CLIENT / TENANT
    ↓
COMPANY INFORMATION
    ↓
CREATE INITIAL ERP ADMIN
    ↓
ASSIGN ERP ADMIN ROLE
    ↓
ACTIVATE TENANT
    ↓
ERP ADMIN LOGIN
    ↓
COMPANY SETUP
    ↓
CREATE USERS
    ↓
ASSIGN ROLES
    ↓
CREATE MASTERS
    ↓
START ERP TRANSACTIONS

All steps must use real database records. No fake success messages.

---

### 14. AUTHENTICATION

Remove hardcoded authentication. Passwords must never be stored in plaintext; use secure password hashing.

**Implement:**
- Login
- Logout
- Current User
- Session/Token
- Authentication expiration
- Unauthorized handling

**Conceptually:**
* `POST /auth/login`
* `POST /auth/logout`
* `GET  /auth/me`

Use the existing project architecture where possible.

---

### 15. EXISTING LOGIN UI

Do not redesign the existing login screen. Connect the existing login form to the real API:

EXISTING LOGIN UI
        ↓
AUTH API
        ↓
FASTAPI
        ↓
POSTGRESQL
        ↓
USER VERIFICATION
        ↓
AUTHENTICATION
        ↓
EXISTING DASHBOARD

No hardcoded credentials.

---

### 16. USER MANAGEMENT

**ERP Admin should be able to:**
- Create, edit, activate, and deactivate users
- Reset passwords
- Assign roles, branches, departments, and warehouses
- Manage permissions

Users must belong to the correct tenant.

---

### 17. ROLES

Create reusable role architecture.

**Initial roles:**
- `SUPER_ADMIN`
- `ERP_ADMIN`
- `ERP_USER`

**Prepare for:**
- `SALES`
- `PURCHASE`
- `FINANCE`
- `WAREHOUSE`
- `INVENTORY`
- `HR`
- `PRODUCTION`
- `QUALITY`
- `CRM`
- `AUDITOR`
- `READ_ONLY`

Do not create unnecessary roles unless required.

---

### 18. PERMISSIONS

Implement centralized permission enforcement.

**Supported actions may include:**
- `VIEW`
- `CREATE`
- `EDIT`
- `DELETE`
- `SUBMIT`
- `APPROVE`
- `REJECT`
- `CANCEL`
- `PRINT`
- `EXPORT`
- `IMPORT`

**Example:**
* `organization.read`, `organization.create`, `organization.update`, `organization.activate`
* `user.read`, `user.create`, `user.update`, `user.deactivate`
* `customer.read`, `customer.create`, `customer.update`, `customer.delete`

Backend authorization is mandatory. Frontend permission hiding is only a UX feature.

---

### 19. AUDIT LOG

Replace static audit records with real audit records.

**Track important events:**
`LOGIN`, `LOGOUT`, `ORGANIZATION_CREATED`, `ORGANIZATION_UPDATED`, `ORGANIZATION_ACTIVATED`, `ORGANIZATION_DEACTIVATED`, `USER_CREATED`, `USER_UPDATED`, `ROLE_ASSIGNED`, `PERMISSION_CHANGED`

**Audit should include:**
- User
- Tenant
- Action
- Module
- Entity & Entity ID
- Timestamp
- Old Value & New Value

---

### 20. COMPANY SETUP

**ERP Admin setup should include:**
- Company
- Branch
- Warehouse
- Fiscal Year
- Currency
- Tax
- Chart of Accounts
- Payment Terms
- Numbering Series

All configuration must belong to the correct tenant.

---

### 21. COMMON MASTERS

Convert existing static/master screens into real functionality.

**At minimum:**
- Customer, Supplier, Item, Item Group, Category, Brand
- UOM & UOM Conversion
- Price List, Tax, Payment Terms
- Warehouse, Branch, Territory, Sales Person, Cost Center
- Chart of Accounts

Do not redesign existing master screens. Connect them:

Existing UI
 ↓
API
 ↓
Business Logic
 ↓
PostgreSQL

---

### 22. CUSTOMER MASTER

**Customer should support:**
Customer Code, Name, Group, Type, Contact, Phone, Email, Billing Address, Shipping Address, Tax Information, Currency, Price List, Payment Terms, Credit Limit, Sales Person, Territory, Accounting Account, Status.

**Connection flow:**

CUSTOMER
 ↓
QUOTATION
 ↓
SALES ORDER
 ↓
DELIVERY
 ↓
SALES INVOICE
 ↓
PAYMENT
 ↓
CUSTOMER LEDGER
 ↓
RECEIVABLE REPORT

---

### 23. SUPPLIER MASTER

**Supplier should support:**
Supplier Code, Name, Group, Contact, Phone, Email, Address, Tax Information, Currency, Payment Terms, Bank Details, Payable Account, Status.

**Connection flow:**

SUPPLIER
 ↓
PURCHASE REQUISITION
 ↓
RFQ
 ↓
SUPPLIER QUOTATION
 ↓
PURCHASE ORDER
 ↓
GOODS RECEIPT
 ↓
PURCHASE INVOICE
 ↓
PAYMENT
 ↓
SUPPLIER LEDGER

---

### 24. ITEM MASTER

**Item should support:**
Item Code, Item Name, Category, Group, Brand, Item Type, UOM, Barcode, SKU, Purchase Price, Sales Price, Tax, Batch Tracking, Serial Tracking, Expiry Tracking, Reorder Level, Minimum Stock, Maximum Stock, Status.

**Connection flow:**

PURCHASE
 ↓
RECEIPT
 ↓
INVENTORY
 ↓
SALES
 ↓
DELIVERY
 ↓
INVOICE
 ↓
STOCK LEDGER

---

### 25. INVENTORY

Implement real inventory functionality.

**Support:**
Opening Stock, Stock Receipt, Stock Issue, Stock Transfer, Stock Adjustment, Stock Reservation, Stock Release, Stock Return, Warehouse Stock, Batch Stock, Serial Stock, Stock Ledger.

Do not simply change frontend quantities. Every stock movement must create a real inventory transaction.

---

### 26. PURCHASE

**Required workflow:**

Supplier
 ↓
Purchase Requisition
 ↓
RFQ
 ↓
Supplier Quotation
 ↓
Purchase Order
 ↓
Approval
 ↓
Goods Receipt
 ↓
Stock Update
 ↓
Purchase Invoice
 ↓
Accounts Payable
 ↓
Payment

Use the existing UI.

---

### 27. SALES

**Required workflow:**

Customer
 ↓
Quotation
 ↓
Sales Order
 ↓
Approval
 ↓
Stock Reservation
 ↓
Delivery
 ↓
Stock Reduction
 ↓
Sales Invoice
 ↓
Accounts Receivable
 ↓
Payment

Use the existing UI.

---

### 28. ACCOUNTING

Accounting must be connected to actual ERP transactions.

**Sales Flow:**
SALES INVOICE
 ↓
ACCOUNTS RECEIVABLE
 ↓
REVENUE
 ↓
TAX
 ↓
GENERAL LEDGER

**Purchase Flow:**
PURCHASE INVOICE
 ↓
ACCOUNTS PAYABLE
 ↓
EXPENSE / INVENTORY
 ↓
TAX
 ↓
GENERAL LEDGER

Do not create disconnected accounting screens.

---

### 29. DASHBOARD

Keep the existing dashboard design. Replace mock values with real database values when the underlying functionality exists. Do not display fake financial information. If real data does not exist yet, show an appropriate empty state.

---

### 30. REPORTS

Reports must query real data.

**Required reports may include:**
Sales, Purchase, Inventory, Customer Ledger, Supplier Ledger, Receivables, Payables, General Ledger, Profit/Loss, Stock, Tax.

Do not hardcode report values.

---

### 31. WORKFLOW ENGINE

Documents should have real statuses.

**Example status progression:**

DRAFT
 ↓
SUBMITTED
 ↓
PENDING_APPROVAL
 ↓
APPROVED
 ↓
PROCESSING
 ↓
COMPLETED

Also support appropriate `REJECTED` and `CANCELLED` statuses. Prevent invalid status transitions.

---

### 32. SEARCH / FILTER / PAGINATION

**Existing tables should support:**
- Search & Filters
- Sorting & Pagination
- Status filtering & Date filtering
- Export where appropriate

Use server-side processing for large datasets.

---

### 33. TESTING STRATEGY

Every module must be tested:

* **Unit Tests:** Business logic, calculations, validation, permission rules.
* **API Tests:** CRUD operations, authorization, validation.
* **Database Tests:** Relationships, constraints, tenant ownership, migrations.
* **Integration Tests:** Complete business workflows.
* **End-to-End Tests:** Workflows tested through the actual UI.

---

### 34. TENANT SECURITY TEST

1. **Create:** `Tenant A` and `Tenant B`
2. **Users:** `adminA@test.local` and `adminB@test.local`
3. **Verify:** User A → Tenant A; User B → Tenant B
4. **Test:**
   - A cannot read, update, or delete B
   - B cannot read, update, or delete A
5. **Coverage:** Test through Frontend and Backend API. Tenant isolation must be enforced server-side.

---

### 35. GIT STRATEGY

**Recommended branches:**
`main`, `develop`, `feature/*`, `bugfix/*`, `release/*`

**Flow:**

feature/*
 ↓
develop
 ↓
QA
 ↓
release/*
 ↓
main
 ↓
production

Do not develop directly on production.

---

### 36. DEV / QA / DEMO / PRODUCTION

* **DEV:** Used by developers and AI-assisted development. Contains experimental features, test data, and debug logs. Must never contain real customer production data.
* **QA:** Used for internal, integration, regression, security, and migration testing.
* **DEMO:** Used by sales teams and prospective customers. Contains controlled sample data. Demo data must never mix with production data.
* **PRODUCTION:** Contains real client business data. Never reset, delete production data, use demo/test data, disable auth/tenant isolation, or run unsafe migrations here.

---

### 37. PRODUCTION DEPLOYMENT

**Deployment flow:**

DEVELOPER
 ↓
GIT
 ↓
AUTOMATED TESTS
 ↓
BUILD
 ↓
DEV
 ↓
TEST
 ↓
QA
 ↓
TEST
 ↓
DEMO
 ↓
TEST
 ↓
HUMAN APPROVAL
 ↓
PRODUCTION

Never automatically deploy untested AI-generated code to production.

---

### 38. DATABASE BACKUPS

**Production must have:**
- Automatic Backups
- Backup Retention
- Recovery Procedure
- Restore Testing

Do not consider production ready without a recovery plan. Never test restore procedures against the only production database.

---

### 39. PRODUCTION SECURITY

**Production must include:**
- HTTPS, Authentication, Authorization, Tenant Isolation
- Password Hashing, Secure Session/Token Handling
- CORS Restrictions, Input Validation, Rate Limiting
- Secure Headers, Secret Management, Audit Logging
- Database Access Restrictions

**Never expose:** Database Passwords, JWT Secrets, API Keys, Cloud Credentials, SMTP Passwords.

---

### 40. LOGGING

* **Development:** Detailed developer logs.
* **QA:** Test/debug logs.
* **Demo:** Operational logs.
* **Production:** Structured logs (Application, Security, Audit, Error).

Never expose stack traces to production users.

---

### 41. MONITORING

**Production should monitor:**
Application/Database Health, API Errors, Response Time, CPU/Memory/Disk utilization, Database Connections, Authentication Failures, Background Jobs.

Health endpoints must provide meaningful service status.

---

### 42. FRONTEND ENVIRONMENT CONFIGURATION

The frontend must not hardcode API URLs.

**Conceptually:**
- **DEV** → DEV API
- **QA** → QA API
- **DEMO** → DEMO API
- **PRODUCTION** → PRODUCTION API

The existing UI must remain visually unchanged.

---

### 43. AI DEVELOPMENT SAFETY

AI is being used to develop this ERP.

**AI may automatically perform:**
Coding, Refactoring, Testing, API development, Migration generation, Bug fixes, Documentation, Frontend/backend integration, Safe configuration changes.

**AI must NOT automatically:**
Delete production data, Drop production tables, Reset production database, Expose secrets, Bypass authentication/authorization/tenant isolation, Destroy existing UI, Replace PostgreSQL, Destroy production infrastructure.

---

### 44. DESTRUCTIVE ACTION APPROVAL GATE

The AI **MUST STOP** and request explicit human approval before:

- Dropping production tables
- Deleting production data or production tenants
- Executing destructive migrations
- Resetting production database or changing production database architecture
- Disabling authentication or tenant isolation
- Replacing production infrastructure or exposing secrets

Safe additive development can continue automatically.

---

### 45. AUTONOMOUS IMPLEMENTATION MODE

Operate in **AUTONOMOUS SEQUENTIAL IMPLEMENTATION MODE**.

**Process:**

AUDIT
 ↓
PLAN
 ↓
IMPLEMENT
 ↓
MIGRATE
 ↓
TEST
 ↓
FIX
 ↓
RETEST
 ↓
REGRESSION
 ↓
VERIFY
 ↓
CHECKPOINT
 ↓
NEXT PHASE

Do not require the user to repeatedly say `continue` when the next phase is safe and all dependencies are satisfied.

---

### 46. PHASE STATES

**Use statuses:**
`NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `TESTING`, `FAILED`, `READY_FOR_NEXT_PHASE`, `COMPLETED`

**Every phase must contain:**
Objective, Tasks, Dependencies, Implementation, Tests, Definition of Done, Status, Known Issues, Checkpoint.

---

### 47. AUTOMATIC ERROR RECOVERY

When a normal development error occurs:

IMPLEMENT
 ↓
TEST
 ↓
FAIL
 ↓
ANALYZE
 ↓
FIX
 ↓
TEST AGAIN

Normal errors (TypeScript, Python, imports, migrations, validations, APIs, DB relationships, UI integration, test failures) may be automatically fixed. Do not hide failures.

---

### 48. ERROR RETRY LIMIT

Do not enter an infinite repair loop. After reasonable attempts at the same critical issue, set `STATUS = BLOCKED` and report:

- Error & Root Cause
- Attempts Made
- Files Changed
- Recommended Resolution

Do not invent unsafe workarounds.

---

### 49. EXISTING UI REGRESSION

After every major phase verify:
- Existing UI loads
- Navigation & Routes work
- Styles & Forms remain intact
- Tables & Dashboards remain intact
- No critical console errors or broken imports

The existing UI must remain recognizable.

---

### 50. IMPLEMENTATION ROADMAP

Execute in this exact order:

* **PHASE 00 — AUDIT:** Inspect the existing project. *(Status: COMPLETED)*
* **PHASE 01 — POSTGRESQL + DATABASE FOUNDATION:** Implement PostgreSQL, connections, migrations, initial schema, seed system, database health.
  * *Definition of Done:* PostgreSQL PASS, Connection PASS, Migration PASS, Seed PASS.
* **PHASE 02 — AUTHENTICATION:** Password hashing, Login, Logout, Current user, Session/token, Auth middleware. Connect existing login screen.
* **PHASE 03 — SUPER ADMIN:** Platform authentication, Organization management (CRUD), Tenant activation/deactivation.
* **PHASE 04 — TENANT / ORGANIZATION:** Tenant model, context, resolution, ownership, isolation.
* **PHASE 05 — ERP ADMIN:** ERP Admin creation, login, company profile & setup.
* **PHASE 06 — USERS + ROLES + PERMISSIONS:** Users, Roles, Permissions, Role assignment, Permission enforcement.
* **PHASE 07 — BRANCH + WAREHOUSE + CONFIGURATION:** Branches, Warehouses, Fiscal year, Currency, Tax, Payment terms, Numbering series, Company settings.
* **PHASE 08 — COMMON MASTERS:** Customer, Supplier, Item, Item Group, Category, Brand, UOM, UOM Conversion, Price List, Tax, Payment Terms, Territory, Sales Person, Cost Center, Chart of Accounts.
* **PHASE 09 — INVENTORY:** Opening stock, Receipt, Issue, Transfer, Adjustment, Reservation, Release, Return, Batch, Serial, Stock ledger.
* **PHASE 10 — PURCHASE:** Requisition → RFQ → Supplier Quotation → Purchase Order → Approval → Goods Receipt → Purchase Invoice → Payment.
* **PHASE 11 — SALES:** Quotation → Sales Order → Approval → Reservation → Delivery → Invoice → Payment.
* **PHASE 12 — ACCOUNTING:** Accounts Receivable, Accounts Payable, General Ledger, Journals, Payments, Taxes, Reconciliation.
* **PHASE 13 — REPORTS:** Real reports for Sales, Purchase, Inventory, Receivables, Payables, Ledgers, P&L, Stock, Tax.
* **PHASE 14 — DASHBOARD:** Replace mock dashboard data with real data.
* **PHASE 15 — ADVANCED ERP MODULES:** Implement only required modules (CRM, Manufacturing, Quality, HR, Payroll, Assets, Projects, Service, POS).
* **PHASE 16 — AUTOMATED TESTING:** Unit, API, Integration, Tenant, Permission, End-to-end, and Regression tests.
* **PHASE 17 — DEV DEPLOYMENT:** Deploy to `dev-erp.<company-domain>`. Verify build, API, DB, auth, isolation, migrations, UI.
* **PHASE 18 — QA DEPLOYMENT:** Deploy tested version to QA. Run full regression.
* **PHASE 19 — DEMO DEPLOYMENT:** Deploy approved version to Demo. Seed safe demonstration data.
* **PHASE 20 — PRODUCTION INFRASTRUCTURE:** Server, HTTPS, DNS, PostgreSQL, Backups, Secrets, Monitoring, Logging, Deployment process.
* **PHASE 21 — PRODUCTION READINESS:** Verify security, auth, isolation, database, backups, restore, migrations, monitoring, logging, performance, regression.
* **PHASE 22 — FIRST CLIENT ONBOARDING:** Super Admin → Create Client Tenant → Create ERP Admin → Activate Tenant → ERP Admin Login → Setup → Users → Roles → Masters → Transactions.

---

### 51. CHECKPOINT SYSTEM

After every completed phase create:

========================================
ERP IMPLEMENTATION CHECKPOINT
========================================

Phase:
Status:

Existing UI:        PASS/FAIL
Backend:            PASS/FAIL
Database:           PASS/FAIL
Migration:          PASS/FAIL
Business Logic:     PASS/FAIL
Authentication:     PASS/FAIL
Authorization:      PASS/FAIL
Tenant Isolation:   PASS/FAIL
Audit:              PASS/FAIL
Tests:              PASS/FAIL
Regression:         PASS/FAIL
Security:           PASS/FAIL

Known Issues:
Files Changed:
Database Changes:
API Changes:
Next Phase:
========================================

Never mark a phase completed if critical tests fail.

---

### 52. FINAL END-TO-END BUSINESS TEST

The complete system must eventually support:

SUPER ADMIN LOGIN
 ↓
CREATE CLIENT TENANT
 ↓
CREATE ERP ADMIN
 ↓
ERP ADMIN LOGIN
 ↓
COMPANY SETUP
 ↓
CREATE ERP USER
 ↓
ASSIGN ROLE
 ↓
ASSIGN PERMISSIONS
 ↓
CREATE CUSTOMER
 ↓
CREATE SUPPLIER
 ↓
CREATE ITEM
 ↓
CREATE WAREHOUSE
 ↓
PURCHASE
 ↓
GOODS RECEIPT
 ↓
INVENTORY UPDATE
 ↓
SALES ORDER
 ↓
DELIVERY
 ↓
SALES INVOICE
 ↓
PAYMENT
 ↓
ACCOUNTING
 ↓
LEDGER
 ↓
REPORTS
 ↓
DASHBOARD

Every step must use real database data.

---

### 53. PRODUCTION TENANT TEST

1. **Create:** `Tenant A`, `Tenant B`, `Tenant C`
2. **Verify:** Tenant A → only A; Tenant B → only B; Tenant C → only C.
3. **Test across:** Customers, Suppliers, Items, Inventory, Sales, Purchases, Invoices, Payments, Accounting, Reports.

Cross-tenant access must fail.

---

### 54. PRODUCTION DEPLOYMENT RULE

Never deploy directly from experimental AI development into production.

**Required:**

DEV
 ↓
TEST
 ↓
QA
 ↓
DEMO
 ↓
HUMAN APPROVAL
 ↓
PRODUCTION

Production deployment requires explicit human approval.

---

### 55. PRODUCTION SAFETY

The AI must stop before executing:
`DROP TABLE`, `DELETE PRODUCTION DATA`, `RESET PRODUCTION DATABASE`, `DELETE PRODUCTION TENANT`, `DESTRUCTIVE MIGRATION`, `DISABLE AUTHENTICATION`, `DISABLE TENANT ISOLATION`, `EXPOSE SECRET`, or `REPLACE PRODUCTION INFRASTRUCTURE`.

Ask for explicit approval.

---

### 56. FIRST EXECUTION

Do **NOT** attempt to implement every ERP module immediately. Start from the current checkpoint.

**Current state:**
- **Audit:** COMPLETED
- **UI:** EXISTING AND EXTENSIVE
- **Backend:** PARTIAL FASTAPI
- **PostgreSQL:** CONFIGURED BUT NEEDS TO BE VERIFIED/RUNNING
- **Authentication:** HARDCODED
- **Migrations:** MISSING
- **Tenant Isolation:** MISSING
- **Permissions:** NOT ENFORCED
- **Audit:** STATIC
- **Dashboard:** MOCK DATA

**Execution Order:**

START
 ↓
PHASE 01 — POSTGRESQL + DATABASE FOUNDATION
 ↓
PHASE 02 — AUTHENTICATION
 ↓
PHASE 03 — SUPER ADMIN
 ↓
PHASE 04 — TENANT
 ↓
PHASE 05 — ERP ADMIN
 ↓
PHASE 06 — USERS / ROLES / PERMISSIONS
 ↓
PHASE 07 — COMPANY CONFIGURATION
 ↓
PHASE 08 — COMMON MASTERS
 ↓
PHASE 09 — INVENTORY
 ↓
PHASE 10 — PURCHASE
 ↓
PHASE 11 — SALES
 ↓
PHASE 12 — ACCOUNTING
 ↓
PHASE 13 — REPORTS
 ↓
PHASE 14 — DASHBOARD
 ↓
PHASE 15 — ADVANCED MODULES
 ↓
PHASE 16 — AUTOMATED TESTING
 ↓
PHASE 17 — DEV
 ↓
PHASE 18 — QA
 ↓
PHASE 19 — DEMO
 ↓
PHASE 20 — PRODUCTION
 ↓
PHASE 21 — PRODUCTION READINESS
 ↓
PHASE 22 — FIRST CLIENT

Do not skip the foundation.

---

### 57. FIRST EXECUTION INSTRUCTION

Before making major changes:
1. Inspect the repository.
2. Inspect Docker.
3. Inspect PostgreSQL configuration.
4. Inspect environment files.
5. Inspect migrations.
6. Inspect seed scripts.
7. Inspect FastAPI.
8. Inspect React.
9. Inspect existing authentication.
10. Inspect existing organization implementation.

Then produce a short implementation plan.

* Do not destroy existing code.
* Do not redesign the UI.
* Do not switch databases.
* Do not reset existing data.
* Fix PostgreSQL/database foundation first.

---

### 58. DEFINITION OF A FULLY FUNCTIONAL ERP

The ERP is **NOT** considered complete merely because pages exist, buttons exist, forms open, tables display, or dashboard looks good.

It is functional only when:

UI
 ↓
API
 ↓
Validation
 ↓
Authorization
 ↓
Business Logic
 ↓
Database
 ↓
Audit
 ↓
Workflow
 ↓
Reports

...all work together.

---

### 59. GOLDEN RULE

The final commercial ERP must be:

ONE ERP PRODUCT
+
ONE MAINTAINABLE CODEBASE
+
DEV
+
QA
+
DEMO
+
PRODUCTION
+
MULTIPLE CLIENT TENANTS
+
STRICT TENANT ISOLATION
+
REAL DATABASE
+
REAL AUTHENTICATION
+
REAL PERMISSIONS
+
REAL BUSINESS LOGIC
+
REAL WORKFLOWS
+
REAL ACCOUNTING
+
REAL REPORTS
+
BACKUPS
+
SECURITY
+
AUDIT
+
AUTOMATED TESTING
+
CONTROLLED DEPLOYMENT

The existing UI remains the source of truth. The AI must improve and connect the existing system rather than destroying and rebuilding it. The ERP must be developed phase-by-phase, tested phase-by-phase, checkpointed phase-by-phase and deployed through controlled environments.

---

**END OF MASTER ERP IMPLEMENTATION + TESTING + DEPLOYMENT PROMPT**
