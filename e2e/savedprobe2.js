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
  const probe = async (label, screen, fill) => {
    await page.evaluate(s => go(s), screen); await sleep(2000);
    await page.evaluate(fill); await sleep(300);
    const info = await page.evaluate(() => { const scr = document.querySelector(".scr.on"); const bs = [...scr.querySelectorAll("button")].filter(b => /^\W{0,3}\s*(save|update)/i.test(b.textContent.trim()) && b.offsetParent); return bs.map(b => b.textContent.trim().replace(/\s+/g, " ")); });
    await page.evaluate(() => { const scr = document.querySelector(".scr.on"); const b = [...scr.querySelectorAll("button")].filter(b => /^\W{0,3}\s*(save|update)\b/i.test(b.textContent.trim()) && !/&/.test(b.textContent) && b.offsetParent)[0]; if (b) { window.__pb = b; b.click(); } });
    await sleep(2500);
    const after = await page.evaluate(() => ({ still: !!window.__pb && window.__pb.isConnected, saved: !!window.__pb && window.__pb.classList.contains("btn-saved"), screen: (document.querySelector(".scr.on") || {}).id, toast: (document.querySelector(".toast, #toast") || {}).innerText }));
    console.log(label, JSON.stringify(info), JSON.stringify(after));
  };
  const setIn = (sel, v) => `(() => { const e = document.querySelector('${sel}'); if (e) { e.value = '${v}'; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); } })()`;
  await probe("depot master", "inv-depot-detail", `(() => { const s = document.querySelector('#s-inv-depot-detail'); const i = s.querySelectorAll('input.fc'); i[0].value = 'D1'; i[1].value = 'Depot One'; i.forEach(x => x.dispatchEvent(new Event('input', {bubbles:true}))); })`);
  await probe("quotation list", "quotation", `(() => {})`);
  await probe("booking new", "booking-new", `(() => {})`);
  await probe("org new", "org-detail", `(() => {})`);
  await probe("country master", "country-master", `(() => {})`);
  await b.close(); process.exit(0);
})();
