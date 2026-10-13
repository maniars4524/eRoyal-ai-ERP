# UI audit: eSway ERP 2.0 (reference HTML `esway-erp.html`)

Generated from the HTML itself. 90 screens; the UI calls only 6 API endpoints (`/api/auth/*`, `/api/customers`, `/api/ports`, `/api/shipments`).


## Platform and tenancy

**Backend status:** Organizations: list/create API exists (Super Admin). Org detail/new forms (66 fields) not wired. Users (real accounts, roles, password reset), employees, branches, departments, designations: IMPLEMENTED - VERIFIED. Rate/route: Route Master IMPLEMENTED - VERIFIED.

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| org-list | Organizations | 2 | 1 | 3 | 1 |
| org-new | New Organization | 66 | 5 | 25 | 10 |
| org-detail | Organization Detail | 0 | 2 | 4 | 6 |
| user-master | User Master | 3 | 1 | 9 | 6 |
| user-detail | User Profile | 37 | 1 | 9 | 5 |
| branch-master | Branch Master | 1 | 1 | 3 | 1 |
| branch-detail | Branch Detail | 141 | 1 | 20 | 3 |
| dept-list | Department Master | 1 | 1 | 2 | 1 |
| dept-new | New Department | 8 | 0 | 3 | 0 |
| desig-list | Designation Master | 2 | 1 | 12 | 10 |
| desig-new | New Designation | 6 | 0 | 3 | 0 |
| emp-list | Employee Master | 3 | 1 | 23 | 6 |
| emp-new | New Employee | 62 | 1 | 15 | 6 |
| emp-detail | Employee Profile | 19 | 3 | 14 | 8 |

## Reference masters

**Backend status:** IMPLEMENTED - VERIFIED (API tests + headless UI test against PostgreSQL): Country, Currency (main list), Unit, Pack, Incoterm, HSN/SAC, Container Type, plus Department, Designation and Branch under Platform. Also implemented: Port Master, Customer Master. Charge Master (modal, HSN/SAC mandatory) is IMPLEMENTED - VERIFIED. NOT IMPLEMENTED: Terms & Conditions, Chart of Accounts, Currency sub-tabs (country mapping, exchange rates), Country/Port/ICD linkages.

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| country-master | Country Master | 3 | 1 | 12 | 8 |
| country-detail | Country Detail | 68 | 2 | 12 | 12 |
| currency-master | Currency Master | 10 | 6 | 31 | 33 |
| currency-detail | Currency Detail | 13 | 0 | 2 | 0 |
| port-master | Port Master | 3 | 1 | 3 | 1 |
| port-detail | Port Detail | 19 | 0 | 2 | 0 |
| container-master | Container Type Master | 2 | 0 | 12 | 0 |
| container-detail | Container Detail | 25 | 0 | 3 | 0 |
| unit-master | Unit Master | 0 | 7 | 46 | 29 |
| unit-detail | Unit Detail | 9 | 0 | 2 | 0 |
| pack-master | Pack Master | 2 | 1 | 20 | 16 |
| pack-detail | Package Detail | 5 | 0 | 2 | 0 |
| incoterms-master | Incoterms Master | 0 | 1 | 13 | 11 |
| incoterm-detail | Incoterm Detail | 9 | 0 | 2 | 0 |
| hsn-master | SAC / HSN Master | 3 | 1 | 14 | 10 |
| hsn-detail | HSN/SAC Detail | 9 | 1 | 3 | 4 |
| charge-master | Charge Master | 0 | 1 | 2 | 10 |
| terms-master | Terms & Conditions Master | 1 | 1 | 7 | 0 |
| coa | Chart of Accounts | 0 | 0 | 3 | 0 |

## Rates and routes

**Backend status:** NOT IMPLEMENTED

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| rate-master | Rate Master | 1 | 1 | 3 | 1 |
| rate-sheet-detail | Rate Sheet Detail | 13 | 1 | 3 | 4 |
| route-list | Route Master | 1 | 1 | 2 | 1 |
| route-new | New Route | 10 | 0 | 3 | 0 |

## CRM and sales

**Backend status:** Quotations IMPLEMENTED - VERIFIED (API + UI). Bookings: backend IMPLEMENTED - VERIFIED, UI NOT YET WIRED. CRM, Meetings, Quotation Templates: NOT IMPLEMENTED

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| crm-lead | Leads | 0 | 0 | 0 | 0 |
| crm-enquiry | Enquiries | 0 | 0 | 0 | 0 |
| crm-opportunity | Opportunities | 0 | 0 | 0 | 0 |
| crm-engagement | Engagement | 0 | 0 | 0 | 0 |
| meetings | Meeting Follow-ups | 0 | 0 | 4 | 0 |
| quotation | Quotations | 0 | 1 | 2 | 1 |
| quotation-new | New Quotation | 13 | 1 | 10 | 1 |
| quotation-print | Quotation Print | 0 | 3 | 5 | 10 |
| quot-templates | Quotation Templates | 0 | 0 | 0 | 0 |
| booking | Bookings | 0 | 1 | 2 | 1 |
| booking-new | New Booking | 44 | 1 | 8 | 0 |

## Jobs and operations

**Backend status:** Jobs list/create/detail header: IMPLEMENTED. Job sub-tabs (containers, BL, customs, charges, tracking, documents, vouchers, manifest) and all operations screens: NOT IMPLEMENTED.

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| job-list | Jobs | 4 | 1 | 4 | 1 |
| job-new | New Job | 32 | 0 | 6 | 0 |
| job-detail | Job Detail | 67 | 10 | 119 | 7 |
| customs | Customs / CHA | 0 | 1 | 1 | 1 |
| tracking | Tracking | 1 | 1 | 0 | 1 |
| transport | Transport | 0 | 1 | 2 | 1 |
| warehouse | Warehouse | 0 | 1 | 2 | 1 |
| svc-console | Shipment Console | 0 | 0 | 0 | 0 |
| svc-cha | CHA | 0 | 0 | 0 | 0 |
| svc-transport | Transport | 0 | 0 | 0 | 0 |
| svc-warehouse | Warehouse | 0 | 0 | 0 | 0 |
| import-air-manifest | Air Manifest | 0 | 1 | 2 | 0 |
| import-sea-manifest | Sea Manifest | 0 | 1 | 2 | 0 |
| import-csn | CSN Filing | 0 | 1 | 2 | 0 |
| bl-draft | BL Draft | 0 | 1 | 4 | 0 |
| freight-cert | Freight Certificate | 0 | 2 | 4 | 8 |
| cargo-arrival | Cargo Arrival Notice | 0 | 1 | 3 | 2 |

## Billing and accounts

**Backend status:** NOT IMPLEMENTED

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| invoice-list | Invoices | 1 | 1 | 6 | 1 |
| invoice-new | Invoice | 8 | 1 | 6 | 4 |
| tax-invoice-print | Tax Invoice | 2 | 5 | 5 | 25 |
| debit-note-list | Debit Notes | 1 | 1 | 5 | 1 |
| debit-note-new | New Debit Note | 15 | 1 | 5 | 1 |
| debit-note-detail | Debit Note Detail | 15 | 6 | 9 | 28 |
| purchase-invoice-list | Purchase Invoices | 1 | 1 | 5 | 1 |
| purchase-invoice-new | New Purchase Invoice | 13 | 1 | 5 | 1 |
| purchase-invoice-detail | Purchase Invoice Detail | 17 | 3 | 8 | 8 |
| vendor-bill | Vendor Bills | 0 | 1 | 5 | 1 |
| payment | Payments | 0 | 1 | 3 | 1 |
| journal-voucher | Journal Voucher | 6 | 1 | 5 | 1 |
| journal-voucher-new | Journal Voucher — New | 9 | 1 | 4 | 0 |
| contra-voucher | Contra Voucher | 6 | 1 | 5 | 1 |
| contra-voucher-new | Contra Voucher — New | 9 | 1 | 4 | 0 |
| pr-ledger | Payable / Receivable Ledger | 7 | 1 | 4 | 0 |
| gst-report | GST Report | 5 | 1 | 7 | 0 |

## Reports, dashboard, settings

**Backend status:** Dashboard: values blank until modules exist. Reports and AI/enterprise configuration: NOT IMPLEMENTED.

| Screen | Title | Inputs | Tables | Buttons | Static dummy rows |
|---|---|---|---|---|---|
| dashboard | Dashboard | 7 | 0 | 23 | 0 |
| pnl | P&L Report | 0 | 1 | 2 | 5 |
| job-pl-report | Job P&L Report | 2 | 5 | 5 | 30 |
| reports | MIS Reports | 0 | 1 | 29 | 0 |
| sop | SOPs | 0 | 0 | 1 | 0 |
| entcfg | Enterprise System Configuration | 1 | 0 | 2 | 0 |
| ai-template-config | AI Template Configuration | 0 | 1 | 2 | 0 |
| ai-workflow-config | AI Workflow Configuration | 276 | 26 | 164 | 35 |


## Build order
1. Generic tenant master framework (server-validated, audited): Currency, Country, Unit, Pack, Incoterms, HSN/SAC, Container Type, Charge, Terms, Department, Designation, Branch.
2. Users and Employees; Organization detail.
3. Rates/Routes, Quotations, Bookings.
4. Job sub-modules (charges, containers, BL, documents, tracking).
5. Invoices, debit/credit notes, purchase invoices, payments, vouchers, ledgers, GST.
6. Reports and dashboard on real data; CRM; operations screens.
