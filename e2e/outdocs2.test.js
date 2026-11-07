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
  const org = await j("POST", "/api/organizations", { code: "O2" + tag, name: "Wiz " + tag, admin_email: `o2${tag}@t.test`, admin_username: "o2" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "o2" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const put = (job, sec, data) => j("PUT", "/api/jobs/" + job + "/details/" + sec, { data }, token);
  const openOD = async n => { await page.evaluate(n => go("job-detail", { jobNo: n }), n); await sleep(3500); await page.evaluate(() => document.querySelector('#s-job-detail .tbb[data-tab="jt7b"]').click()); await sleep(2000); };
  const gen = async (code, wait) => { await page.evaluate(c => document.querySelector('[data-od2="gen"][data-code="' + c + '"]').click(), code); await sleep(wait || 4500); };
  const sea = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA(CHENNAI)", pod: "NLRTM(ROTTERDAM)", etd: "2030-05-01", eta: "2030-05-30" }, token)).body.job_number;
  await put(sea, "parties", { shipper: "ABC Trading Co", consignee: "Global Importers Ltd", notify1: "Global Importers Ltd" });
  await put(sea, "cargo", { pkgs: "2", gwt: "25000", nwt: "24000", vol: "65", cargoDesc: "Auto parts" });
  await put(sea, "containers", [{ container_no: "TCLU1234567", type: "40HC", seal_no: "ABC12345", pkgs: "2", gross_wt: "25000", net_wt: "24000" }, { container_no: "TCLU7654321", type: "40HC", seal_no: "ABC67890", pkgs: "0", gross_wt: "0", net_wt: "0" }]);
  const bl = await put(sea, "bl", { bookingType: "House", hblType: "MTO", mblNo: "MBL9990001", mblDate: "2030-05-01" }); console.log("BL", bl.status);
  await j("POST", "/api/jobs/" + sea + "/routing/legs", { carrier: "MSC Orion", voyage: "VOY12345", pol: "Chennai", pod: "Singapore", etd: "2030-05-01", eta: "2030-05-10", stage: "CONFIRMED" }, token);
  await j("POST", "/api/jobs/" + sea + "/routing/legs", { carrier: "Maersk Bella", voyage: "VOY67890", pol: "Singapore", pod: "Rotterdam", etd: "2030-05-12", eta: "2030-05-30", stage: "PLANNED" }, token);
  await openOD(sea);
  const pages = () => page.evaluate(() => document.querySelectorAll("#modalBody .pg").length);
  for (const [code, label] of [["sailing_confirmation", "Sailing Confirmation"], ["shipment_summary", "Shipment Summary"], ["pre_alert", "Pre-alert"]]) {
    await gen(code);
    if (code === "shipment_summary") { await page.evaluate(() => { document.querySelector('input[name="odVar"][value="without_rates"]').checked = true; document.getElementById("odVarGo").click(); }); await sleep(3000); }
    const dlg = await text("#modalBody");
    check(label + ": the generate dialog offers the signature choice", /signature and seal/i.test(dlg), dlg.slice(0, 120));
    await page.evaluate(() => document.getElementById("odInGo").click()); await sleep(4500);
    check(label + " is a single page", (await pages()) === 1, String(await pages()));
    await page.evaluate(() => closeModal()); await sleep(500);
  }
  await gen("hbl");
  const hb = await text("#modalBody");
  check("the HBL has Gross Weight, Net Weight and Measurement columns with the net weight shown", /Gross Weight/.test(hb) && /Net Weight/.test(hb) && /Measurement/.test(hb) && /NET\. WT\. KGS\s*24000/.test(hb.replace(/\n/g, " ")) && /GR\. WT\. KGS\s*25000/.test(hb.replace(/\n/g, " ")), hb.replace(/\s+/g, " ").slice(300, 700));
  await page.evaluate(() => closeModal()); await sleep(400);
  // air export: HAWB with the conditions of contract
  const air = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", container_mode: "Air", pol: "MAA", pod: "SIN" }, token)).body.job_number;
  await put(air, "parties", { shipper: "Natural Remedies Pvt Ltd", consignee: "AGRO TRADE SRL" });
  await put(air, "cargo", { pkgs: "1", gwt: "678", cargoDesc: "TEST" });
  await openOD(air);
  await gen("hawb", 6500);
  const hw = await text("#modalBody");
  check("the HAWB is two pages: the air waybill and the conditions of contract", (await pages()) === 2 && /NOTICE CONCERNING CARRIER'S LIMITATION OF LIABILITY/.test(hw) && /CONDITIONS OF CONTRACT/.test(hw) && /MONTREAL CONVENTION/.test(hw), String(await pages()));
  check("the HAWB face has the IATA layout: account numbers, agent IATA code, routing, flight/date, declared values, charges block, both signatures and the HAWB number", ["Shipper's Account Number", "Consignee's Account Number", "Agent's IATA Code", "Requested Flight/Date", "Declared Value for Carriage", "Declared Value for Customs", "Amount of Insurance", "Total Other Charges Due Agent", "Total Other Charges Due Carrier", "Currency Conversion Rates", "For Carrier's Use only at Destination", "Signature of Shipper or his Agent", "Signature Of Issuing Carrier or its Agent", "HAWB NO:"].every(t => hw.includes(t)), hw.replace(/\s+/g, " ").slice(0, 200));
  // printing the HAWB: Draft / A4 / Pre print / Multiple print
  await page.evaluate(() => { window.__w = []; window.open = () => { const o = { html: "", document: { write: h => { o.html += h; }, close: () => {} }, focus: () => {}, print: () => {} }; window.__w.push(o); return o; }; });
  const pr = async (mode, afterPick) => { await page.evaluate(() => document.querySelector('[data-od2="printp"]').click()); await sleep(600); await page.evaluate(m => document.querySelector('[data-od-pm="' + m + '"]').click(), mode); await sleep(500); if (afterPick) await afterPick(); await sleep(600); return page.evaluate(() => window.__w[window.__w.length - 1].html); };
  const pv = async () => { await page.evaluate(() => document.querySelector('#odDyn [data-od2="view"][data-code="hawb"]').click()); await sleep(3000); };
  await pv();
  const cnt = (h, re) => (h.match(re) || []).length;
  const dr = await pr("draft");
  check("Draft print marks every page DRAFT", cnt(dr, /DRAFT/g) >= 2 && cnt(dr, /class="pg/g) === 2);
  await pv(); const a4 = await pr("a4");
  check("A4 print has both pages and no DRAFT mark", cnt(a4, /class="pg/g) === 2 && !/>DRAFT</.test(a4) && !/Page \d+ of \d+/.test(a4));
  await pv(); const pp = await pr("preprint");
  check("Pre print is the first page only, with the lines and printed text hidden", cnt(pp, /class="pg/g) === 1 && /preprint/.test(pp) && /visibility:hidden/.test(pp) && !/SPECIAL DRAWING RIGHT/.test(pp));
  await pv(); const mp = await pr("multiple", () => page.evaluate(() => { document.querySelectorAll("[data-od-cp]").forEach(e => { e.checked = /SHIPPER COPY|AGENT COPY/.test(e.parentElement.textContent) && !/ORIGINAL/.test(e.parentElement.textContent); }); document.getElementById("odCopiesGo").click(); }));
  check("Multiple print prints each chosen copy marked SHIPPER COPY / AGENT COPY, both pages each", /SHIPPER COPY/.test(mp) && /AGENT COPY/.test(mp) && !/ORIGINAL 1/.test(mp) && cnt(mp, /class="pg/g) === 4, String(cnt(mp, /class="pg/g)));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
