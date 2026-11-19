// Overseas invoice: titled OVERSEAS INVOICE (USD), own number series, optional local (INR) amount when printing, listed in the export report with its taxable value
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); let b = null; try { b = await r.json(); } catch (e) {} return { status: r.status, body: b }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "OV" + tag, name: "Ov " + tag, admin_email: `ov${tag}@t.test`, admin_username: "ov" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ov" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI" }, token);
  const usd = { charge_name: "Detention Charges", description: "YHVU0987654- ( 12 DAYS )", hsn: "996799", uom: "Per Shipment", qty: "1", ccy: "USD", exch: "83", rate: "1000", tax_type: "Taxable", tax_pct: "18" };
  const doc = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "OVERSEAS BUYER BV", supply_type: "inter", place_of_supply: "Outside India", lines: [usd] }, token)).body;
  let r = await j("POST", `/api/documents/${doc.id}/post`, null, token); if (r.status >= 400) { await j("POST", `/api/documents/${doc.id}/confirm`, null, token); r = await j("POST", `/api/documents/${doc.id}/post`, null, token); }
  check("posted with an OIN number", r.status === 200 && /^OIN\//.test(r.body.doc_number), JSON.stringify(r.body).slice(0, 200));
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1300 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  for (const tpl of ["one", "two"]) {
    await page.evaluate(i => window.DocKit.view("doc", { docId: i }), doc.id); await sleep(2500);
    if (tpl === "two") { await page.select("#dkTpl", "two"); await sleep(2500); }
    const t = (await text("#docViewZone")).replace(/\s+/g, " ");
    check("Template " + tpl + ": the header says OVERSEAS INVOICE (USD), not TAX INVOICE", /OVERSEAS INVOICE \(USD\)/i.test(t) && !/TAX INVOICE/.test(t.replace(/Tax Invoice/g, "")), (t.match(/.{60}TAX INVOICE.{60}/) || [t.slice(0, 100)])[0]);
    check("Template " + tpl + ": the local amount is not shown until asked", !/Local amount \(INR\)/.test(t));
    await page.evaluate(() => { const c = document.querySelector("#dkLocal"); c.checked = true; c.dispatchEvent(new Event("change", { bubbles: true })); }); await sleep(2500);
    const t2 = (await text("#docViewZone")).replace(/\s+/g, " ");
    check("Template " + tpl + ": 'Local amount (INR)' with the rate 83.0000 and INR 83,000.00", /Local amount \(INR\)/.test(t2) && /83\.0000/.test(t2) && /83,000\.00/.test(t2), t2.slice(-400));
  }
  const g = (await j("GET", "/api/gl/gst", null, token)).body;
  const row = (g.sales || []).find(x => /^OIN\//.test(x.number));
  check("the GST register has the overseas invoice with taxable 83,000.00 and IGST 0.00", !!row && Number(row.taxable) === 83000 && Number(row.igst) === 0, JSON.stringify(row));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
