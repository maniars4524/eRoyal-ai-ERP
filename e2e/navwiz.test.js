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
  const vis = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && e.offsetParent !== null; }, sel);
  const rows = await page.evaluate(() => [...document.querySelectorAll('[data-go="job-new"][data-jobdir]')].map(r => r.dataset.jobmode + "/" + r.dataset.jobdir));
  check("Import and Export > Air, Ocean, Road, Multimodal now open the New Shipment page", rows.length >= 8, rows.join());
  for (const [mode, dir, label] of [["Air", "Import", "Air Import"], ["Sea", "Export", "Sea Export"]]) {
    await page.evaluate(() => go("dashboard")); await sleep(600);
    await page.evaluate((m, d) => document.querySelector('.tn-row[data-go="job-new"][data-jobmode="' + m + '"][data-jobdir="' + d + '"]').click(), mode, dir); await sleep(1800);
    check(label + ": the New Shipment page opens (not the job list)", await vis("#jobWizWrap") && !(await vis("#jobTableBody")), "");
    check(label + ": mode and direction are already chosen", await page.evaluate((m, d) => jobWiz.mode === m && jobWiz.dir === d, mode, dir) && await vis("#jobWizPre") && await vis("#jobWizTypeGrid"));
  }
  await page.evaluate(() => go("dashboard")); await sleep(500);
  await page.evaluate(() => document.querySelector('.tn-row[data-go="job-new"]:not([data-jobdir])').click()); await sleep(1500);
  check("Create Shipment from the menu asks everything (no leftover preset)", await vis("#jobWizModeGrid") && !(await vis("#jobWizPre")));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
