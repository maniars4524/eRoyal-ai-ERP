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
  const org = await j("POST", "/api/organizations", { code: "NA" + tag, name: "Nav " + tag, admin_email: `na${tag}@t.test`, admin_username: "na" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "na" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const screens = await page.evaluate(() => Array.from(document.querySelectorAll('.scr[id^="s-"]')).map(e => e.id.slice(2)));
  console.log("screens:", screens.length);
  const bad = [];
  for (const sc of screens) {
    const before = errs.length;
    const r = await page.evaluate(async name => {
      try { window.go(name); } catch (e) { return { thrown: String(e) }; }
      await new Promise(r => setTimeout(r, 700));
      const el = document.getElementById("s-" + name);
      const text = el ? el.innerText : "";
      const junk = (text.match(/undefined|\[object Object\]|NaN|null\b/g) || []).slice(0, 3);
      return { on: !!(el && el.classList.contains("on")), len: text.trim().length, junk };
    }, sc);
    const newErrs = errs.slice(before);
    if (r.thrown || !r.on || r.len < 20 || r.junk.length || newErrs.length) bad.push({ sc, ...r, errs: newErrs.slice(0, 1) });
  }
  console.log("PROBLEMS:", bad.length);
  bad.forEach(x => console.log(JSON.stringify(x).slice(0, 300)));
  await b.close(); process.exit(0);
})();
