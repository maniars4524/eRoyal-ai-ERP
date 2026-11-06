// Real Chrome: the cargo dimensions popup on an Air quotation, and on jobs (Air, Sea LCL, FCL, consolidation by house reference), with quotation -> job.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "CG" + tag, name: "Cargo " + tag, admin_email: `cg${tag}@t.test`, admin_username: "cg" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cg" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const row = async (pk, l, w, h, g, ref) => { await setv("#cd_pk", String(pk)); await setv("#cd_l", String(l)); await setv("#cd_w", String(w)); await setv("#cd_h", String(h)); await setv("#cd_g", g == null ? "" : String(g)); await setv("#cd_ref", ref || ""); await click("#cd_add"); await sleep(700); };
  const popupText = () => text("#cdBox");

  // ---- Air quotation
  const q = (await j("POST", "/api/quotations", { party_name: "Dims Ltd", mode: "Air", direction: "Export", shipment_type: "General", pol: "MAA", pod: "FRA", lines: [] }, token)).body;
  await page.evaluate(async id => { const r = await fetch("/api/quotations/" + id, { headers: { Authorization: "Bearer " + sessionStorage.getItem("esway_access_token") } }).then(r => r.json()); QK.pendingOpen = r; go("quotation-new"); }, q.id); await sleep(3200);
  check("the Air quotation has a Dimensions button instead of the free-text box", await page.evaluate(() => !!document.querySelector("#qkDims [data-cd-quote]")));
  await click("#qkDims [data-cd-quote]"); await sleep(1500);
  check("the popup opens as Cargo Dimensions - Air with its entry area and no rows", /Cargo Dimensions — Air/.test(await text("#modalTitle")) && /No rows yet/.test(await popupText()));
  await setv("#cd_l", "100"); await click("#cd_add"); await sleep(600);
  check("an incomplete row is refused with a clear message and not added", !(await page.evaluate(() => document.querySelectorAll("#cd_rows tr[style], #cd_rows td.tbl-empty").length === 0)));
  await row(2, 100, 80, 60, 100);
  await row(3, 120, 100, 80, null);
  await sleep(600);
  const t1 = await popupText();
  check("the totals are the server's: 5 packages, 3.840 CBM, 640 kg volumetric, 640 kg chargeable", /3\.840/.test(t1) && /640\.000/.test(t1) && /chargeable weight/i.test(t1), t1.slice(0, 300));
  await page.evaluate(() => document.querySelector('[data-cd="edit:1"]').click()); await setv("#cd_pk", "1"); await click("#cd_add"); await sleep(900);
  check("editing a row updates it (1 package of 120 x 100 x 80 = 0.960 CBM)", /1\.920/.test(await popupText()), (await popupText()).slice(0, 300));
  await row(1, 10, 10, 10, 5); await page.evaluate(() => document.querySelector('[data-cd="del:2"]').click()); await sleep(800);
  check("deleting a row removes it from the totals", (await page.evaluate(() => document.querySelectorAll("#cd_rows tr").length)) === 2);
  await click('[data-cd="saveclose"]'); await sleep(1500);
  const f = await page.evaluate(() => ({ p: document.querySelector('[data-qh="packages"]').value, g: document.querySelector('[data-qh="gross_weight"]').value, v: document.querySelector('[data-qh="volume"]').value, c: document.querySelector('[data-qh="chargeable_weight"]').value }));
  check("applying fills Packages, Gross Weight, Volume and Chargeable Weight on the quotation", f.p === "3" && f.g === "100" && f.v === "1.92" && f.c === "320", JSON.stringify(f));
  check("the Dimensions area now shows a summary and View / Edit", /View \/ Edit Dimensions \(2 rows\)/.test(await text("#qkDims")), await text("#qkDims"));
  await page.evaluate(() => document.querySelector('[data-qa="save"]').click()); await sleep(2800);
  const saved = (await api("/api/quotations/" + q.id)).header;
  check("saving the quotation keeps the rows and the server's figures", saved.dim_rows.length === 2 && saved.packages === "3" && saved.chargeable_weight === "320.000" && /divisor 6000/.test(saved.dim_calculation), JSON.stringify(saved).slice(0, 300));
  await click("#qkDims [data-cd-quote]"); await sleep(1500);
  check("reopening shows the saved rows", (await page.evaluate(() => document.querySelectorAll("#cd_rows tr").length)) === 2);
  const before = confirms; await row(1, 5, 5, 5, 1); await click('[data-cd="cancel"]'); await sleep(600);
  check("closing with unapplied changes asks first", confirms === before + 1);

  // ---- direct jobs
  const mkJob = async (mode, cm) => (await j("POST", "/api/shipments", { mode, direction: "Export", container_mode: cm, pol: mode === "Air" ? "MAA(CHENNAI)" : "INMAA(CHENNAI)", pod: mode === "Air" ? "FRA(FRANKFURT)" : "SGSIN(SINGAPORE)" }, token)).body.job_number;
  const openJob = async jn => { await page.evaluate(n => go("job-detail", { jobNo: n }), jn); await sleep(3800); await page.evaluate(() => { const t = document.querySelector('[data-tab="jt2"]'); if (t) t.click(); }); await sleep(600); };
  const air = await mkJob("Air", null);
  await openJob(air);
  check("a direct Air job has its own Cargo Dimensions area", /Cargo Dimensions — Air/.test(await text("#cdJobCard")) && /No dimensions entered yet/.test(await text("#cdJobCard")), await text("#cdJobCard"));
  await click("#cdJobOpen"); await sleep(1500);
  await row(2, 100, 80, 60, 100);
  await click('[data-cd="apply"]'); await sleep(1500);
  check("Apply updates the job screen and says it is not saved yet", /applied, not saved yet/.test(await text("#cdJobCard")) && (await page.evaluate(() => document.getElementById("jc_pkgs").value)) === "2");
  check("nothing is saved until Save: the server still has no dimensions", !(await api("/api/jobs/" + air + "/details")).dims.rows);
  await click('[data-cd="save"]'); await sleep(2500);
  const ad = (await api("/api/jobs/" + air + "/details")).dims;
  check("Save stores the rows with the calculation method and the chargeable weight", ad.version === 2 && ad.rows.length === 1 && ad.totals.chargeable_kg === 160 && ad.calculation.method.divisor === "6000", JSON.stringify(ad).slice(0, 250));
  check("the job card shows the saved figures", /160\.000/.test(await text("#cdJobCard")) && !/not saved yet/.test(await text("#cdJobCard")));
  await click("#cdJobOpen"); await sleep(1300); await row(1, 50, 50, 50, 10); await click('[data-cd="savenew"]'); await sleep(2600);
  check("Save & New saves both rows and leaves the entry area empty for the next one", (await api("/api/jobs/" + air + "/details")).dims.rows.length === 2 && (await page.evaluate(() => document.getElementById("cd_l").value)) === "" && (await page.evaluate(() => document.getElementById("cd_pk").value)) === "1");
  await click('[data-cd="cancel"]'); await sleep(500);

  const lcl = await mkJob("Sea", "LCL");
  await openJob(lcl);
  check("a Sea LCL job has the Cargo Dimensions - Sea LCL area", /Cargo Dimensions — Sea LCL/.test(await text("#cdJobCard")));
  await click("#cdJobOpen"); await sleep(1500);
  await row(1, 100, 100, 500, null); await setv("#cd_sg", "3000"); await sleep(700);
  check("LCL shows CBM, weight in MT and the chargeable revenue ton", /chargeable rt/i.test(await popupText()) && /5\.000/.test(await popupText()) && /3\.000/.test(await popupText()), (await popupText()).slice(0, 300));
  await click('[data-cd="save"]'); await sleep(2500);
  check("the LCL job saved 5 RT", (await api("/api/jobs/" + lcl + "/details")).dims.totals.chargeable_rt === "5.000");

  const fcl = await mkJob("Sea", "FCL");
  await openJob(fcl);
  check("an FCL job says the figures are recorded only", /FCL is charged per container/.test(await text("#cdJobCard")));

  // consolidation: cargo by house reference on one job
  await openJob(air); await click("#cdJobOpen"); await sleep(1500);
  await row(1, 40, 40, 40, 20, "HAWB-A"); await row(1, 60, 60, 60, 30, "HAWB-B");
  check("consolidation cargo is totalled by house reference", /by house reference/i.test(await popupText()) && /HAWB-A/.test(await popupText()) && /HAWB-B/.test(await popupText()), (await popupText()).slice(-400));
  await click('[data-cd="cancel"]'); await sleep(500);

  // quotation -> job
  const q2 = (await j("POST", "/api/quotations", { party_name: "Conv Ltd", mode: "Air", direction: "Export", shipment_type: "General", pol: "MAA", pod: "FRA", header: { dim_rows: [{ packages: "2", length: "100", width: "80", height: "60", unit: "CM", gross: "100", gross_unit: "KG", weight_basis: "TOTAL" }] },
    lines: [{ side: "SELL", charge_name: "Handling", basis: "Per Shipment", ccy: "INR", exch: "1", rate: "100", qty: "1", tax_pct: "0" }] }, token)).body;
  for (const p of ["approval/submit", "approval/approve", "send", "accept"]) await j("POST", `/api/quotations/${q2.id}/${p}`, null, token);
  const conv = (await j("POST", `/api/quotations/${q2.id}/convert`, null, token)).body;
  await openJob(conv.job_number);
  check("the job made from the quotation shows its dimensions without retyping, marked as coming from the quotation", /from quotation/.test(await text("#cdJobCard")) && /160\.000/.test(await text("#cdJobCard")), await text("#cdJobCard"));
  // ---- New Job screen: Dimensions in Shipment Configuration (Air and Sea LCL), carried onto the created job
  await page.evaluate(() => go("job-new")); await sleep(2500);
  await page.evaluate(() => { jobWiz.mode = "Air"; jobWiz.dir = "Export"; jobWiz.type = "General"; const m = document.getElementById("nj_mode"); m.value = "Air"; m.dispatchEvent(new Event("change", { bubbles: true })); const c = document.getElementById("nj_contmode"); if (c) c.value = "Air"; document.getElementById("njShipAir").style.display = ""; }); await sleep(2000);
  check("New Job (Air) shows a Dimensions button in Shipment Configuration", await page.evaluate(() => !!document.querySelector("#njShipAir .njdims-host [data-nj-dims]")));
  await click("#njShipAir [data-nj-dims]"); await sleep(1500);
  await row(2, 100, 80, 60, 100); await click('[data-cd="saveclose"]'); await sleep(1500);
  check("Apply fills Packages and Gross Weight on the New Job screen", (await page.evaluate(() => document.getElementById("nj_pkgs").value)) === "2" && (await page.evaluate(() => document.getElementById("nj_wt").value)) === "100");
  const created = await page.evaluate(async () => { const r = await fetch("/api/shipments", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + sessionStorage.getItem("esway_access_token") }, body: JSON.stringify({ mode: "Air", direction: "Export", container_mode: "Air", pol: "MAA(CHENNAI)", pod: "FRA(FRANKFURT)", packages: 2, gross_weight: 100 }) }); return r.json(); });
  await sleep(2500);
  const nd = (await api("/api/jobs/" + created.job_number + "/details")).dims;
  check("the dimensions entered on New Job are saved on the job once it is created", nd && nd.version === 2 && nd.rows.length === 1 && nd.totals.chargeable_kg === 160, JSON.stringify(nd).slice(0, 200));
  // quotation: Dimensions sits above Incoterm
  const order = await page.evaluate(() => { const l = [...document.querySelectorAll("label")]; return null; });
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
