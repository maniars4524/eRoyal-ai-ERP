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

  const mkJob = async (mode, cm) => (await j("POST", "/api/shipments", { mode, direction: "Export", container_mode: cm, pol: mode === "Air" ? "MAA(CHENNAI)" : "INMAA(CHENNAI)", pod: mode === "Air" ? "FRA(FRANKFURT)" : "SGSIN(SINGAPORE)" }, token)).body.job_number;
  const openJob = async jn => { await page.evaluate(n => go("job-detail", { jobNo: n }), jn); await sleep(3800); await page.evaluate(() => { const t = document.querySelector('[data-tab="jt2"]'); if (t) t.click(); }); await sleep(600); };

  const api2 = p => j("GET", p, null, token).then(r => r.body);
  const lcl = await mkJob("Sea", "LCL");
  await openJob(lcl);
  await click("#cdJobOpen"); await sleep(1500);
  await row(2, 100, 80, 60, 150); await row(3, 50, 50, 50, 90);
  await click('[data-cd="save"]'); await sleep(2800);
  const d1 = await api2("/api/jobs/" + lcl + "/details");
  console.log("LCL cargo:", JSON.stringify(d1.cargo)); console.log("LCL containers:", JSON.stringify(d1.containers)); console.log("LCL dims totals:", JSON.stringify(d1.dims.totals));
  console.log("LCL screen containers table:", await page.evaluate(() => Array.from(document.querySelectorAll("#containerTableBody tr")).map(t => Array.from(t.querySelectorAll("input,select")).map(i => i.dataset.k + "=" + i.value).filter(x => /container_no|type|pkgs|gross_wt|vol/.test(x)).join(" ")).join(" || ")));
  const air = await mkJob("Air", null);
  await openJob(air); await click("#cdJobOpen"); await sleep(1500);
  await row(2, 100, 80, 60, 100); await row(1, 50, 50, 50, 40);
  await click('[data-cd="save"]'); await sleep(2800);
  const d2 = await api2("/api/jobs/" + air + "/details");
  console.log("AIR cargo:", JSON.stringify(d2.cargo)); console.log("AIR dims totals:", JSON.stringify(d2.dims.totals));
  console.log("AIR screen:", await page.evaluate(() => ["jc_pkgs", "jc_gwt", "jc_vol", "jc_chg"].map(i => i + "=" + ((document.getElementById(i) || {}).value || "-")).join(" ")));
  await b.close(); process.exit(0);
})();
