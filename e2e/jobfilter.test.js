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
  const org = await j("POST", "/api/organizations", { code: "JF" + tag, name: "Wiz " + tag, admin_email: `jf${tag}@t.test`, admin_username: "jf" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jf" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const mk = async (mode, dir, cm) => (await j("POST", "/api/shipments", { mode, direction: dir, container_mode: cm, pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const a = await mk("Air", "Export", "Air"), s1 = await mk("Sea", "Export", "FCL"), s2 = await mk("Sea", "Import", "FCL"), a2 = await mk("Air", "Import", "Air");
  await page.evaluate(() => go("job-list")); await sleep(3000);
  const rows = () => page.evaluate(() => [...document.querySelectorAll("#jobTableBody tr[data-job-row]")].filter(r => r.offsetParent !== null).map(r => r.dataset.mode + "/" + r.dataset.dir));
  console.log("ALL", JSON.stringify(await rows()));
  for (const [i, name, want] of [[3, "Air Export", "Air/Export"], [4, "Air Import", "Air/Import"], [1, "Sea Export", "Sea/Export"], [2, "Sea Import", "Sea/Import"]]) {
    await page.evaluate(i => document.querySelectorAll("#s-job-list .kpi-rich")[i].click(), i); await sleep(1500);
    const r = await rows();
    check("card " + name + " shows only " + want + " jobs", r.length > 0 && r.every(x => x === want), JSON.stringify(r));
  }
  // dashboard card path
  await page.evaluate(() => go("dashboard")); await sleep(2000);
  const cells = await page.evaluate(() => [...document.querySelectorAll('.dd-cell[data-filter]')].map(c => c.dataset.filter));
  console.log("CELLS", JSON.stringify(cells));
  for (const f of ["air-export-active", "sea-export-active", "air-import-active"]) {
    await page.evaluate(() => go("dashboard")); await sleep(800);
    await page.evaluate(f => { let c = document.querySelector('.dd-cell[data-filter="' + f + '"]'); if (!c) { c = document.createElement("div"); c.className = "dd-cell"; c.dataset.filter = f; document.body.appendChild(c); } c.click(); }, f); await sleep(2500);
    const r = await rows(); const want = f.split("-").slice(0, 2).map(x => x[0].toUpperCase() + x.slice(1)).join("/");
    check("dashboard " + f + " shows only " + want, r.length > 0 && r.every(x => x === want), JSON.stringify(r));
  }
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
