# Honesty audit - information in the ERP that is not real (2026-11-05)

Review of `esway-erp.html` and the backend. STATUS (2026-11-05, second pass): items marked DONE below are fixed and live; the rest is open.
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


## Status after the clean-up
- A1 DONE: the chat no longer invents a person. Header says "eSway Help - ready-made answers, not a live person"; asking for support says live support chat is not connected and to ask the company administrator.
- A2 DONE: help answers about the dashboard, settings, approvals, tracking and outstanding corrected to what exists; "AI Assistant" renamed "eSway Help" (it is a keyword list, not AI).
- A3 DONE: the CSN draft starts empty (no invented submitter code, PANs, ICEGATE user, IRN, vessel code, ports, weights, HS code, container, seal, addresses, random HBL no). The built-in validation now reports each blank as missing. Also fixed: "Download started" really downloads the JSON now; "CSN saved" now says plainly it is kept for this browser session only; "Valid ... ready to submit" reworded; a banner on the screen says it is a draft tool and nothing is sent to ICEGATE.
- A4 DONE: example hints made generic (IEC, pincode, CTC, department email).
- B6 DONE: "Load sample clauses" button removed from the HBL clause master (the backend endpoint still exists, unused).
- B7 DONE: the dead sample-figure chart slides (Tata Chemicals, TVS Motor, sales executive names, branch revenue) deleted. A static Branch Manager drop-down with invented names (Rajan Kumar, Suresh Nair, Priya Menon) was also found and emptied.
- B5 OPEN (your decision): the ERP Demo portal on the login page uses sample data. It is labelled, so left alone.
- C8-C10 OPEN: screen-by-screen check of static HTML screens ("demo dataset" comments), KPI cards that show 0 instead of "No Data Available", and reference lists.
- NEW, OPEN: the CSN filing screen is front-end only (kept in memory, lost on reload, not sent anywhere). It is now labelled so; making it real needs a backend table and the ICEGATE connection.
- NEW, OPEN: other static forms may still contain fixed drop-down lists that look like company data (e.g. region / department / role options). These are option lists, not records, so left for the screen-by-screen pass.
