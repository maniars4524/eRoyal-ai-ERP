// Air export: the charges of the Charges tab appear in the boxes of the House Air Waybill (weight, valuation, tax, due agent, due carrier, other charges text, totals); the BL form lists them and lets the user move or hide one
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
  const org = await j("POST", "/api/organizations", { code: "AC" + tag, name: "Ac " + tag, admin_email: `ac${tag}@t.test`, admin_username: "ac" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ac" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "MVMLE" }, token)).body.job_number;
  const put = async (sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(job)}/details/${sec}`, { data }, token)).status;
  await put("parties", { shipper: "MANITOU SOUTH ASIA PVT LTD\nCHENNAI INDIA", consignee: "WASTE MANAGEMENT CORPORATION LIMITED\nMALE" });
  await put("cargo", { pkgs: "1", gwt: "55", cargoDesc: "SPARE PARTS" });
  await put("bl", { bookingType: "House", mblNo: "603-60134734", awb: { airline: "UL", payment: "Prepaid", routingTo: "CMB", currency: "INR" } });
  const ln = (name, rate) => ({ charge_name: name, qty: "1", uom: "Per Shipment", hsn: "", buy: { ccy: "INR", exch: "1", rate: "0", tax_type: "Non-Taxable", tax_pct: "0" }, sell: { ccy: "INR", exch: "1", rate: String(rate), tax_type: "Non-Taxable", tax_pct: "0" } });
  const pc = await j("PUT", `/api/shipments/by-number/${encodeURIComponent(job)}/charges`, { lines: [ln("Air Freight", 17600), ln("Valuation Charge", 50), ln("Service Tax", 10), ln("Handling", 200), ln("CGC", 75), ln("MCC", 150), ln("SC", 300)] }, token);
  check("charges entered in the Charges tab", pc.status === 200, JSON.stringify(pc.body).slice(0, 200));

  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1400 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const norm = t => t.replace(/\s+/g, " ");
  const preview = async fk => { await page.evaluate(jn => { window.__lastJobNo = jn; window.DocKit.view("awb", { job: jn }); }, job); await page.waitForSelector("#dkFmt", { timeout: 15000 }); await sleep(1500); await page.select("#dkFmt", fk); await sleep(2800); return norm(await text("#docViewZone")); };

  // 1. printed straight from the Charges tab
  let t = await preview("three");
  check("weight charge 17,600.00, valuation 50.00, tax 10.00, due agent 200.00, due carrier 525.00", ["17,600.00", "50.00", "10.00", "200.00", "525.00"].every(x => t.includes(x)), t.slice(0, 1200));
  check("Other Charges text is built from the charges (CGC : 75/-  MCC : 150/-  SC : 300/-)", /CGC : 75\/- MCC : 150\/- SC : 300\/-/.test(t), (t.match(/Other Charges.{0,160}/i) || [""])[0]);
  check("Total Prepaid = 17,600 + 50 + 10 + 200 + 525 = 18,385.00", t.includes("18,385.00"), t.slice(-700));
  // 2. the BL form lists them and can move / hide one
  await page.evaluate(n => window.go("job-detail", { jobNo: n }), job); await sleep(2500); await page.evaluate(() => document.querySelector('.tbb[data-tab="jt3"]').click()); await sleep(2500);
  const rows = await page.evaluate(() => [...document.querySelectorAll("#jobAwbCharges tbody tr")].map(r => r.children[0].textContent.trim() + "=" + r.querySelector("select").selectedOptions[0].textContent.replace(/\s+/g, " ")).join(" | "));
  check("the BL form lists the charges with where each goes (Auto)", /Air Freight=Auto \(Weight charge\)/.test(rows) && /Handling=Auto \(Total other charges due agent\)/.test(rows) && /CGC=Auto \(Total other charges due carrier\)/.test(rows) && /Service Tax=Auto \(Tax\)/.test(rows), rows);
  await page.evaluate(() => { const s = document.querySelector('#jobAwbCharges select[data-awbch="handling"]'); s.value = "NONE"; s.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.evaluate(() => { const i = document.querySelector("#jobAwb_wtval"); i.value = "CC"; i.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.evaluate(() => document.querySelector('[data-jd-save="jt3"]').click()); await sleep(2500);
  const saved = (await j("GET", `/api/jobs/${encodeURIComponent(job)}/details`, null, token)).body.bl;
  check("the choice is saved with the waybill (Handling not on the waybill)", saved.awb.chargeMap && saved.awb.chargeMap.handling === "NONE" && saved.awb.wtval === "CC", JSON.stringify(saved.awb.chargeMap));
  t = await preview("one");
  check("a hidden charge is left out; the weight side is collect, the other charges stay prepaid", !t.includes("200.00") && t.includes("17,600.00") && t.includes("525.00"), t.slice(-600));
  const tot = await page.evaluate(() => { const z = document.querySelector("#docViewZone .pg"); return z ? z.innerText.replace(/\s+/g, " ") : ""; });
  check("Total Prepaid 525.00 and Total Collect 17,660.00", /Total Prepaid[^0-9]{0,40}525\.00/.test(tot) && /Total Collect[^0-9]{0,40}17,660\.00/.test(tot), tot.slice(-500));
  // 3. open the Charges tab from the BL form
  await page.evaluate(n => window.go("job-detail", { jobNo: n }), job); await sleep(2000); await page.evaluate(() => document.querySelector('.tbb[data-tab="jt3"]').click()); await sleep(2000);
  await page.evaluate(() => document.querySelector("#jobAwbChargesOpen").click()); await sleep(800);
  check("the button opens the Charges tab", await page.evaluate(() => document.querySelector('.tbb[data-tab="jt5"]').classList.contains("on")));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
