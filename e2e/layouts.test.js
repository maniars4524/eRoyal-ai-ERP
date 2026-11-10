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
  const org = await j("POST", "/api/organizations", { code: "LY" + tag, name: "Lay " + tag, admin_email: `ly${tag}@t.test`, admin_username: "ly" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ly" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const fs2 = require("fs"); fs2.mkdirSync("../scratch/layouts", { recursive: true });
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const put = (job, sec, data) => j("PUT", "/api/jobs/" + job + "/details/" + sec, { data }, token);
  const openOD = async n => { await page.evaluate(n => go("job-detail", { jobNo: n }), n); await sleep(3500); await page.evaluate(() => document.querySelector('#s-job-detail .tbb[data-tab="jt7b"]').click()); await sleep(2000); };
  const gen = async code => { await page.evaluate(c => { const b = document.querySelector('[data-od2="gen"][data-code="' + c + '"]'); if (b) b.click(); }, code); await sleep(4500); };
  const shot = async name => { const h = await page.$("#modalBody"); if (h) { await h.screenshot({ path: "../scratch/layouts/" + name + ".png" }); } };
  const finish = async name => { // press whichever confirm button the dialog shows
    await page.evaluate(() => { document.querySelectorAll("#modalBody input, #modalBody textarea").forEach(e => { if (e.type === "checkbox" || e.type === "radio" || e.value) return; e.value = e.type === "date" ? "2030-06-01" : e.type === "number" ? "5" : "Test value"; e.dispatchEvent(new Event("input", { bubbles: true })); }); });
    for (const id of ["odInGo", "odVarGo", "odCanGo"]) { const ok = await page.evaluate(id => { const b = document.getElementById(id); if (b) { b.click(); return true; } return false; }, id); if (ok) { await sleep(4500); break; } }
    const t = await text("#modalBody"); const pages = await page.evaluate(() => document.querySelectorAll("#modalBody .pg").length);
    await shot(name); console.log(name, "pages=" + pages, "len=" + t.length, /undefined|\[object|NaN/.test(t) ? "JUNK" : "clean"); await page.evaluate(() => closeModal()); await sleep(500); };
  const ln = (name, rate, tt, pct) => ({ charge_name: name, description: "d", uom: "Per Container", qty: "1", hsn: "996512", buy: { ccy: "INR", exch: "1", rate: "0", tax_type: "Non-Taxable", tax_pct: "0", party: "", use_party: false }, sell: { ccy: "INR", exch: "1", rate, tax_type: tt, tax_pct: pct, party: "", use_party: false } });
  const all = async tag => { const codes = await page.evaluate(() => [...document.querySelectorAll('[data-od2="gen"]')].map(b => b.dataset.code)); console.log(tag, "offers:", codes.join(","));
    for (const c of codes) { if (c === "can" || c === "hbl" || c === "hawb") continue; await gen(c); await finish(tag + "_" + c); } };
  const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGQAAAAyCAIAAAAjzRLnAAAAKUlEQVR4nO3BAQ0AAADCoPdPbQ43oAAAAAAAAAAAAAAAAAAAAAAAAAAAvg0hAAABmN0mTAAAAABJRU5ErkJggg==";
  console.log("logo", (await j("PUT", "/api/company/logo", { data_url: PNG }, token)).status, "profile", (await j("PUT", "/api/company/doc-profile", { address: "5/41 5th Cross Street, Balaji Nagar, Chennai - 600032", phone: "044 1234 5678", email: "ops@example.com", gstin: "33AAACI3746A2Z5" }, token)).status);
  // ---- sea export
  const sea = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA(CHENNAI)", pod: "NLRTM(ROTTERDAM)", etd: "2030-05-01", eta: "2030-05-30" }, token)).body.job_number;
  await put(sea, "parties", { shipper: "ABC Trading Co", consignee: "Global Importers Ltd", notify1: "Global Importers Ltd" });
  await put(sea, "cargo", { pkgs: "2", gwt: "25000", nwt: "24000", vol: "65", cargoDesc: "Auto parts" });
  await put(sea, "containers", [{ container_no: "TCLU1234567", type: "40HC", seal_no: "ABC12345", pkgs: "2", gross_wt: "25000", net_wt: "24000" }]);
  await put(sea, "bl", { bookingType: "House", hblType: "MTO", mblNo: "MBL9990001", mblDate: "2030-05-01" });
  await j("POST", "/api/jobs/" + sea + "/routing/legs", { carrier: "MSC Orion", voyage: "VOY12345", pol: "Chennai", pod: "Rotterdam", etd: "2030-05-01", eta: "2030-05-30", stage: "CONFIRMED" }, token);
  await openOD(sea);
  await all("sea_exp");
  // ---- sea import with mixed taxable / non-taxable charges
  const imp = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Import", container_mode: "FCL", pol: "SGSIN", pod: "INMAA", etd: "2030-05-01", eta: "2030-05-20" }, token)).body.job_number;
  await put(imp, "parties", { shipper: "Overseas Seller", consignee: "Chennai Buyer Pvt Ltd" });
  await put(imp, "cargo", { pkgs: "10", gwt: "5000", cargoDesc: "Machinery" });
  await put(imp, "containers", [{ container_no: "MSCU7654321", type: "20GP", seal_no: "S1", pkgs: "10", gross_wt: "5000", net_wt: "4800" }]);
  await j("POST", "/api/jobs/" + imp + "/routing/legs", { carrier: "MSC Orion", voyage: "V1", pol: "Singapore", pod: "Chennai", etd: "2030-05-01", eta: "2030-05-20", stage: "PLANNED" }, token);
  await j("PUT", "/api/shipments/by-number/" + imp + "/charges", { lines: [ln("Terminal Handling Charges", "3000", "Taxable", "18"), ln("Documentation Charges", "750", "Taxable", "18"), ln("Customs Duty Reimbursement", "12000", "Non-Taxable", "0")] }, token);
  await openOD(imp);
  await gen("can"); await page.evaluate(() => { const r = document.querySelector('input[name="odVar"][value="with_rates"]'); if (r) r.checked = true; }); await finish("can_mixed");
  await all("sea_imp");
  // ---- air export / import
  const air = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", container_mode: "Air", pol: "MAA", pod: "SIN" }, token)).body.job_number;
  await put(air, "parties", { shipper: "Natural Remedies Pvt Ltd", consignee: "AGRO TRADE SRL" });
  await put(air, "cargo", { pkgs: "1", gwt: "678", cargoDesc: "TEST" }); await put(air, "bl", { mblNo: "176-12345675", mblDate: "2030-05-02", hblNo: "RTS000123", bookingType: "House" });
  await openOD(air);
  await all("air_exp");
  const airi = (await j("POST", "/api/shipments", { mode: "Air", direction: "Import", container_mode: "Air", pol: "SIN", pod: "MAA" }, token)).body.job_number;
  await put(airi, "bl", { mblNo: "176-99999991", mblDate: "2030-05-02", hblNo: "RTS000999", bookingType: "House" }); await put(airi, "parties", { shipper: "Overseas Seller", consignee: "Chennai Buyer" }); await put(airi, "cargo", { pkgs: "3", gwt: "120", cargoDesc: "Parts" });
  await openOD(airi);
  await all("air_imp");
  console.log("errors:", errs.length, errs.join(" | ").slice(0, 400));
  await b.close(); process.exit(0);
})();
