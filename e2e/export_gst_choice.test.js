// Invoice with Place of supply Outside India: GST nil by default (with a visible reason) and a switch to charge the job-cost tax %; the cost-entry description prints under the charge
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
  const org = await j("POST", "/api/organizations", { code: "EG" + tag, name: "Eg " + tag, admin_email: `eg${tag}@t.test`, admin_username: "eg" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "eg" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI" }, token);
  const line = { charge_name: "Detention Charges", description: "YHVU0987654- ( 12 DAYS )", hsn: "996799", uom: "Per Shipment", qty: "1", ccy: "INR", exch: "1", rate: "1000", tax_type: "Taxable", tax_pct: "18" };
  const doc = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "OVERSEAS BUYER BV", supply_type: "inter", place_of_supply: "Outside India", lines: [line] }, token)).body;
  check("nil GST on a document created for Outside India", Number(doc.tax_total) === 0, JSON.stringify(doc).slice(0, 200));
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1300 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  await page.evaluate(i => window.BK.openDoc(i), doc.id); await sleep(2000);
  check("the editor says why the GST is nil and offers the switch", /Outside India/.test(await text(".scr.on")) && /GST is nil/.test(await text(".scr.on")) && await page.evaluate(() => !!document.querySelector('.scr.on [data-bkh-on="charge_gst"]')));
  await page.evaluate(() => { const c = document.querySelector('.scr.on [data-bkh-on="charge_gst"]'); c.checked = true; c.dispatchEvent(new Event("change", { bubbles: true })); }); await sleep(600);
  check("charging GST brings the tax % back (18) and the tax 180.00 shows in the totals", await page.evaluate(() => { const i = document.querySelector('.scr.on [data-bkl="0"][data-f="tax_pct"]'); return !!i && Number(i.value) === 18; }) && /180\.00/.test(await text(".scr.on")), await text(".scr.on"));
  await page.evaluate(() => document.querySelector('.scr.on [data-bk="save"]').click()); await sleep(2200);
  const saved = (await j("GET", `/api/documents/${doc.id}`, null, token)).body;
  check("saved with GST 180.00 and total 1,180.00", Number(saved.tax_total) === 180 && Number(saved.grand_total) === 1180, JSON.stringify([saved.tax_total, saved.grand_total]));
  for (const tpl of ["one", "two"]) {
    await page.evaluate(i => window.DocKit.view("doc", { docId: i }), doc.id); await sleep(2500);
    if (tpl === "two") { await page.select("#dkTpl", "two"); await sleep(2500); }
    const t = (await text("#docViewZone")).replace(/\s+/g, " ");
    check("Template " + tpl + " prints the cost-entry description under the charge", /Detention Charges/i.test(t) && /YHVU0987654- \( 12 DAYS \)/.test(t), t.slice(0, 300));
    check("Template " + tpl + " shows the tax 180.00", /180\.00/.test(t), t.slice(0, 300));
    check("Template " + tpl + " (draft = proforma, Outside India): titled PROFORMA INVOICE, not OVERSEAS / TAX INVOICE", /PROFORMA INVOICE/.test(t) && !/OVERSEAS INVOICE/.test(t), t.slice(0, 200));
  }
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
