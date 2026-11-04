# Honesty audit - information in the ERP that is not real (2026-11-05)

Read-only review of `esway-erp.html` and the backend. NOTHING has been changed yet; this is the list to decide from.
Line numbers are in `esway-erp.html` as of commit after the filter-bar fix.

## A. Shown to the user as if real (should go)
1. **Fake human support agent** (~13456-13510): "Connecting you to a support representative..." then "Priya S. - Support Representative, Online now", and a reply "I'm looking into this for you now. I'll follow up shortly". There is no human and nobody follows up. The typing delays use Math.random to look human.
2. **Help-bot answers that are out of date** (`CHAT_KB`, ~13363): says the dashboard has "26 slides" and "4 flippable KPI cards"; the carousel now has 3 real slides plus the live Freight / CHA / Transport ones. It also names menu paths that may no longer match.
3. **CSN / Sea manifest filing defaults** (`ensureCsnFiling`, ~10851-10900, `csnBuildJson` ~10904): a new filing is pre-filled with values that look real but are invented: submitter code AAGCE4436R, authorised person PAN AAHCA3717A, consolidated PAN AAC9808SE, ICEGATE user id ROYALTECH898, IRN 9798799999999999, vessel code 123456, transport means id 090909, port INMAA1, a fixed date "2026-08-03", gross weight 12000 / net 11500 when the job has none, HS code 111111, container MEDU898977, seal 22221, shipper/consignee street "ADDR 1 / ADDR 2", city CHENNAI, country code 33, a notify party "NRS FREIGHT SYSTEM" with a Singapore address, itinerary ports (Tughlakabad, Patparganj). A random house BL number is generated with Math.random. If a user files without overwriting them, wrong data goes to customs.
4. **Placeholder examples that look like real data** (input hints, greyed): IEC 0512098765, pincode 600001, CTC 750000, dept@eswayfreight.com. Low risk (they are hints), but IEC is a real-format number.

## B. Labelled, but still not real
5. **ERP Demo portal** ("Explore the ERP system using sample data", ~13590): clearly labelled demo; keep or remove as you decide. Uses its own database `esway_erp_demo`.
6. **"Load sample clauses"** button on HBL clauses (~21563): loads sample text into the real company's clause list.
7. **Hard-coded chart figures** (~12326-12380): Tata Chemicals 620000, TVS Motor 480000, Infosys, GEA West, Raghav Granites etc. Slides 3+ are cut off (`DASH_SLIDES.length = Math.min(..., 3)`) so they should not show; the dead code and any other place using `CHART_DATA` still has these names and numbers. Safe to delete the dead code.

## C. Not yet verified (need a screen-by-screen check)
8. Static HTML screens whose comments say "(demo dataset)": Organisation detail, Job detail, Invoice detail headers (~7153, 7285, 8083). They are meant to be overwritten by real data; confirm none shows old demo values when a field is empty.
9. KPI cards and dashboards that show 0 or a fixed figure when there is no data: they should say "No Data Available" instead.
10. State/city lists (~20525) and drop-downs with fixed options are reference data, not company data - fine, but check they match the Port / State masters.

## D. Checked and fine
- Math.random is used only for ids / tab keys (not for figures).
- Backend has no random or sample data generators; no seeded dummy masters (per the standing rule).
- Rate sheets start empty ("no sample sheets"); document formats show "format not added yet" instead of a sample.

## Suggested clean-up order
1. Remove the fake support agent (A1) - honest message: "Support is not connected yet; email ... ".
2. Blank every invented CSN default (A3) and block filing until the real values are entered.
3. Update or remove the help-bot text (A2).
4. Delete the dead chart data (B7) and remove "Load sample clauses" (B6) if wanted.
5. Walk the screens for C8-C9.
