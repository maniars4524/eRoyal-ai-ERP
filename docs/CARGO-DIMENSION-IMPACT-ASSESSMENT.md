# Cargo dimension, volumetric weight, CBM and chargeable weight - impact assessment

Date: 2026-11-05. Read-only study of the live code; nothing has been changed. Written before any build, as the prompt requires.

## 1. What already exists (so we extend it, not rebuild it)

| Area | What is there today | Where |
|---|---|---|
| Air weights | Package rows -> volumetric weight (cm: L x W x H / 6000; also inch / 166 and a CBM x 167 formula), chargeable = greater of actual and volumetric, **rounded up to the next 0.5 kg**. | `backend/app/air_weight.py` |
| Basis engine | One service for charge quantities: Per Shipment, Per Package, By Gross Wt, **By Chg Wt**, **By Volume (CBM)**, **By Gross Wt/Volume (Air: greater of gross and volumetric; Sea LCL: W/M = greater of tonnes and CBM)**, % of Freight, % of Other, Per Vehicle, By Net Wt. FCL is not touched. Decimal arithmetic. Dimension rows in cm only. | `backend/app/basis_calc.py`, `basis_api.py` |
| Divisor | One global setting (6000), changed only by an environment variable. | `config.py` `AIR_VOLUMETRIC_DIVISOR` |
| Job screen | An Air-only "Package Dimensions & Volumetric Weight" calculator with a rows table; saved per job as JSON (`job_details.dims`). The job's overview and Air Waybill take the chargeable weight from it. Sea shows Containers instead of dimensions. | `esway-erp.html` (dimension calculator, Job Detail kit), `job_details_api.py` |
| Job costing | Charge lines on a chargeable-weight basis take the chargeable KG; Basis lines take quantities from `job_measures()` -> `basis_calc`. The server recalculates on save, so the screen cannot disagree with the books. | `job_charges_api.py` |
| Quotation | Only headline numbers (packages, gross weight, chargeable weight typed by hand for Air) and one **free-text** "Dimensions (L x W x H cm)" box that nothing calculates from. Basis lines already use `basis_calc` with those headline numbers. | `quotations_api.py`, quotation form |
| Quotation -> job | Copies headline gross weight and charges. **No dimension rows are carried over.** | `quotations_api.py` (convert) |
| Accounting | Invoices and vouchers take the quantity from the job charge lines; posting logic is separate and must not change. | `documents_api.py`, `gl.py` |

## 2. Gaps against the prompt

1. **Two calculators**: `air_weight` (job dimensions) and `basis_calc` (charges) hold similar formulas separately. The prompt wants one engine. Risk: they can disagree (for example `air_weight` rounds up to 0.5 kg, `basis_calc` does the same only on one path; row weight is multiplied by packages in `air_weight`).
2. **Units**: only cm (and inch for air). The prompt needs CM, MM, M, INCH, FEET and weight KG / TON.
3. **Row gross weight meaning**: `air_weight` treats the row weight as **per package** (weight x packages). The prompt says not to multiply unless the field is explicitly per package. The field must be labelled, and old saved jobs must keep their meaning.
4. **No admin configuration**: no screen or table for the divisor by carrier / service / customer, express 5000, rounding, minimums, W/M rule, precision.
5. **No dimension popup** with Add / Edit / Delete / Clear / Save / Save & New / Apply / Cancel, and no totals block (packages, CBM, gross, volumetric, chargeable, RT) on the main screen.
6. **Quotation has no dimension rows**, so nothing flows to the job.
7. **Sea LCL jobs have no dimension entry** (Containers tab only), so LCL CBM comes only from a typed volume.
8. **Consolidation is not modelled**: there is no master / house job structure (`master_job` and `house_job` exist only as unused numbering types; the only parent link is CHA job -> freight job). Section 7 of the prompt is therefore a new data model, not an extension.
9. **No audit of the calculation** (divisor, method, configuration and time stored with the result).

## 3. Proposed design (additive, nothing deleted)

- **One engine** `cargo_engine.py`: pure functions (rows + mode + shipment type + unit + configuration -> CBM, volumetric kg, gross kg, MT, chargeable kg, RT, divisor and rule used). `air_weight` and `basis_calc` call it, so job screen, quotation, costing and reports share it. Existing numbers must stay the same: golden tests built from today's outputs before anything is switched over.
- **Storage**: reuse `job_details.dims` for jobs (add fields, keep old shape readable). New additive tables only where needed: `cargo_calc_configs` (divisor / rounding / W/M rule by mode, carrier, service, customer), `quotation_dimensions` (rows + totals + method), `cargo_dimension_sets` for consolidation later. Migrations additive and reversible.
- **Popup**: the existing Cargo Dimensions layout (as in your screenshot) turned into a popup with the actions in the prompt; main screen shows the totals block and a View / Edit Dimensions button.
- **Posted documents are never altered**: a dimension change after invoicing follows the existing amendment / credit-note route; the screen warns first.

## 4. Decisions needed from you

1. **Rounding**: today air chargeable weight is rounded **up to the next 0.5 kg**. Keep that as the default and make it configurable? (Recommended: yes.)
2. **Row gross weight**: from now on "gross weight of this row (total)", with old jobs keeping their per-package meaning through a flag. OK?
3. **LCL W/M rule**: default 1 CBM = 1,000 kg (as the prompt), with carrier / customer overrides in the admin screen. OK?
4. **Consolidation** needs a new master / house job model. Recommend doing it last, as its own phase, after quotation and job costing are done. OK?
5. **Existing quotations**: keep the old free-text dimensions box visible (read-only) beside the new rows? (Recommended: yes.)

## 5. Phases and rough time

| Phase | Content | Time |
|---|---|---|
| 2 | `cargo_engine` + golden tests + the prompt's 10 test cases; wire `air_weight` / `basis_calc` to it with unchanged results | 1 session |
| 3 | Dimension popup on the job screen (Air and Sea LCL), multi-row, Save / Save & New / Apply / Cancel, totals block | 1-2 sessions |
| 4 | Quotation dimension rows, totals, quotation -> job transfer without duplicates | 1-2 sessions |
| 5 | Job costing for Air KG and LCL RT using the engine; override audit | 1 session |
| 7 | Verify invoices, purchase invoices, credit / debit notes and profitability use the same quantities; no posting changes | 0.5 session |
| 8 | Admin configuration screen, full regression, backup / rollback check, deploy | 1 session |
| 6 | Consolidation (new master / house structure and parent totals) | 2-3 sessions |

About 8-11 sessions in all; the first useful result (engine + job popup) is after about 2-3 sessions.

## 6. Protection plan

Fresh database backup before every migration; additive migrations only; golden tests of today's results before the engine is switched in; the accounting and posting modules are not edited; each phase is committed, tested in a separate empty test database and deployed on its own, so any phase can be rolled back alone.
