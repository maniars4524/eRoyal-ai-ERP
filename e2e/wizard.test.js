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
  const org = await j("POST", "/api/organizations", { code: "WZ" + tag, name: "Wiz " + tag, admin_email: `wz${tag}@t.test`, admin_username: "wz" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "wz" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);

  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const vis = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && e.offsetParent !== null; }, sel);
  const parent = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" }, token)).body;
  // from the menu: Operations > Job / Shipment > Import > Air Freight, then New Job
  await page.evaluate(() => document.querySelector('.tn-row[data-go="job-new"][data-jobmode="Air"][data-jobdir="Import"]').click()); await sleep(1800);
  check("coming from Air > Import the mode and direction are not asked again", await vis("#jobWizPre") && !(await vis("#jobWizModeGrid")) && !(await vis("#jobWizDirGrid")) && /Import/.test(await text("#jobWizPre")), await text("#jobWizPre"));
  check("the shipment type list is next, as compact buttons", (await page.evaluate(() => document.querySelectorAll("#jobWizTypeGrid .wiz-seg").length)) === 6);
  await page.evaluate(() => document.querySelector('#jobWizTypeGrid [data-pick="General Cargo"]').click()); await sleep(300);
  check("booking type is Direct / House", (await page.evaluate(() => [...document.querySelectorAll("#jobWizBTGrid .wiz-seg")].map(b => b.textContent).join())) === "Direct,House");
  await page.evaluate(() => document.querySelector('#jobWizBTGrid [data-pick="House"]').click()); await sleep(300);
  check("air House does not ask for OTL / FMC / MTO and Next is ready", !(await vis("#jobWizHouseSubWrap")) && !(await page.evaluate(() => document.getElementById("jobWizNext").disabled)));
  check("Sub Job is a No by default: no pick-list of made-up jobs", !(await vis("#jobWizMasterJob")) && /No — standalone/.test(await text("#jobWizSubSeg")));
  await page.evaluate(() => document.querySelector('#jobWizSubSeg [data-sub="1"]').click()); await sleep(1200);
  const opts = await page.evaluate(() => [...document.querySelectorAll("#jobWizMasterJob option")].map(o => o.value));
  check("Yes lists only the real jobs and Next waits for a choice", opts.includes(parent.job_number) && !opts.includes("SEX-25-000047") && await page.evaluate(() => document.getElementById("jobWizNext").disabled), JSON.stringify(opts));
  await page.evaluate(n => { const s = document.getElementById("jobWizMasterJob"); s.value = n; s.dispatchEvent(new Event("change", { bubbles: true })); }, parent.job_number); await sleep(300);
  check("choosing the main job enables Next", !(await page.evaluate(() => document.getElementById("jobWizNext").disabled)));
  await page.evaluate(() => document.getElementById("jobWizPreChange").click()); await sleep(300);
  check("Change brings the mode and direction back", await vis("#jobWizModeGrid") && await vis("#jobWizDirGrid"));
  // Sea still asks for the House BL type
  await page.evaluate(() => { jobWiz.pre = false; jobWiz.sub = false; document.querySelector('#jobWizModeGrid [data-pick="Sea"]').click(); }); await sleep(300);
  await page.evaluate(() => document.querySelector('#jobWizDirGrid [data-pick="Export"]').click()); await sleep(200);
  await page.evaluate(() => document.querySelector('#jobWizTypeGrid [data-pick="FCL"]').click()); await sleep(200);
  await page.evaluate(() => document.querySelector('#jobWizBTGrid [data-pick="House"]').click()); await sleep(300);
  check("Sea House asks for the House BL type and Next waits", await vis("#jobWizHouseSubWrap") && await page.evaluate(() => document.getElementById("jobWizNext").disabled));
  await page.screenshot({ path: "../scratch/shot_state.png" }); const compact = await page.evaluate(() => document.getElementById("jobWizWrap").getBoundingClientRect().height);
  check("the whole selection fits on one screen (" + Math.round(compact) + "px)", compact < 760, String(compact));
  // the sub job is stored under its parent
  const sub = (await j("POST", "/api/shipments", { mode: "Air", direction: "Import", container_mode: "Air", pol: "FRA", pod: "MAA", parent_job_number: parent.job_number }, token)).body;
  check("the sub job points at its main job", sub.parent_job_id === parent.id, JSON.stringify(sub).slice(0, 120));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
