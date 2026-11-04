// Real Chrome: opening a list from the menu (not by calling go() directly) shows the filter bar with its Company / Status choices.
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
  const org = await j("POST", "/api/organizations", { code: "LM" + tag, name: "Menu " + tag, admin_email: `lm${tag}@t.test`, admin_username: "lm" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "lm" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "LM" + tag, name: "Menu cust " + tag }, token)).body;
  await j("PUT", "/api/crm/lead", { ref: "LD" + tag, customer_id: cust.id, company: cust.name, status: "New" }, token);
  await j("POST", "/api/quotations", { party_name: cust.name, customer_id: cust.id, mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "DEHAM", lines: [{ side: "SELL", charge_name: "OF", basis: "Per Container", ccy: "USD", exch: "83.5", rate: "850", qty: "2", tax_pct: "0", carrier: "" }] }, token);
  await j("POST", "/api/documents", { doc_type: "invoice", customer_id: cust.id, party_name: cust.name }, token);
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1400, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  for (const [s, label] of [["crm-lead", "Leads"], ["crm-enquiry", "Enquiries"], ["quotation", "Quotations"], ["invoice-list", "Invoices"], ["job-list", "Jobs"], ["booking", "Bookings"]]) {
    await page.evaluate(s => { const e = document.querySelector('[data-go="' + s + '"]'); e.click(); }, s); await sleep(2500);
    const r = await page.evaluate(() => { const scr = document.querySelector(".scr.on"), bar = scr.querySelector(".lk-bar"); return { bar: !!bar && bar.getBoundingClientRect().height > 20, size: !!(bar && bar.querySelector('[data-lk="size"]')) }; });
    check(label + ": opened from the menu, the filter bar and Rows selector are visible", r.bar && r.size, JSON.stringify(r));
  }
  const co = await page.evaluate(() => { const o = document.querySelector('.scr.on [data-lk="company"]'); return o ? o.options.length : 0; });
  await page.evaluate(() => document.querySelector('[data-go="invoice-list"]').click()); await sleep(2500);
  check("the Company and Status choices are filled from the list", await page.evaluate(() => document.querySelectorAll('.scr.on [data-lk="company"] option').length > 1 && document.querySelectorAll('.scr.on [data-lk="status"] option').length > 1));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
