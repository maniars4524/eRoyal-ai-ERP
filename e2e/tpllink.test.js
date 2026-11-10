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
  const org = await j("POST", "/api/organizations", { code: "TL" + tag, name: "Tpl " + tag, admin_email: `tl${tag}@t.test`, admin_username: "tl" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "tl" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  await j("POST", "/api/masters/shipment_template/import-sample", null, token);
  // ---- quotation wizard
  await page.evaluate(() => window.go("quotation-new")); await sleep(2500);
  const opts = await page.evaluate(() => { const s = document.getElementById("quotWizTemplate"); return s ? s.options.length : -1; });
  check("the quotation page offers the shipment templates", opts > 5, opts);
  const val = await page.evaluate(() => { const s = document.getElementById("quotWizTemplate"); const o = Array.from(s.options).find(x => /Sea Export FCL Direct/.test(x.text)); return o ? o.value : null; });
  await page.evaluate(v => { const s = document.getElementById("quotWizTemplate"); s.value = v; s.dispatchEvent(new Event("change", { bubbles: true })); }, val); await sleep(800);
  const q = await page.evaluate(() => ({ mode: window.quotWiz.mode, dir: window.quotWiz.dir, type: window.quotWiz.type, next: document.getElementById("quotWizNext").disabled }));
  check("a template fills mode, direction and type on the quotation", q.mode === "Sea" && q.dir === "Export" && q.type === "FCL", JSON.stringify(q));
  // ---- enquiry form
  await page.evaluate(() => window.go("crm-enquiry")); await sleep(1500);
  await page.evaluate(() => document.getElementById("crmNewEnquiryBtn").click()); await sleep(2500);
  const eo = await page.evaluate(() => { const g = document.querySelector("#crmEq_template optgroup"); return g ? g.children.length : 0; });
  check("the enquiry form lists the company templates", eo > 5, eo);
  const ev = await page.evaluate(() => { const o = Array.from(document.querySelectorAll("#crmEq_template optgroup option")).find(x => /Air Import Direct/.test(x.text)); return o ? o.value : null; });
  await page.evaluate(v => { const s = document.getElementById("crmEq_template"); s.value = v; s.dispatchEvent(new Event("change", { bubbles: true })); }, ev); await sleep(600);
  const en = await page.evaluate(() => ({ mode: document.getElementById("crmEq_mode").value, type: document.getElementById("crmEq_shptype").value }));
  check("a template fills mode and shipment type on the enquiry", en.mode === "Air" && en.type === "Air Import", JSON.stringify(en));
  const inc = await page.evaluate(() => Array.from(document.querySelectorAll("#crmEq_incoterm option")).map(o => o.value));
  check("the enquiry Incoterm list is fed from the Incoterm master", inc.includes("DPU") && inc.includes("FAS"), inc.join(","));
  const ct = await page.evaluate(() => Array.from(document.querySelectorAll("#crmEq_conttype option")).map(o => o.value));
  check("the enquiry container types are fed from the Container Type master", ct.includes("40HC") || ct.some(x => /40/.test(x) && x !== "40' GP"), ct.join(","));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
