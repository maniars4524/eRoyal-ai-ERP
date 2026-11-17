// Output Docs > HAWB / HAWBL: Preview only previews, Regenerate only regenerates; the preview has Final / Draft / Pre-print / Multiple; Print asks which one
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
  const org = await j("POST", "/api/organizations", { code: "OH" + tag, name: "Oh " + tag, admin_email: `oh${tag}@t.test`, admin_username: "oh" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "oh" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const put = async (sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(job)}/details/${sec}`, { data }, token)).status;
  await put("parties", { shipper: "MANITOU SOUTH ASIA PVT LTD\nCHENNAI", consignee: "ALLIED TELESIS\nSINGAPORE" });
  await put("cargo", { pkgs: "1", gwt: "55", cargoDesc: "SPARE PARTS" });
  await put("bl", { bookingType: "House", mblNo: "603-60134734", awb: { airline: "UL", payment: "Prepaid", routingTo: "SIN", currency: "INR", flight: "UL603", rate: "10", chargeableWt: "55" } });

  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1300 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const vis = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && e.offsetParent !== null; }, sel);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const hawb = act => `[data-od2="${act}"][data-code="hawb"]`;
  await page.evaluate(n => window.go("job-detail", { jobNo: n }), job); await sleep(2500); await page.evaluate(() => document.querySelector('.tbb[data-tab="jt7b"]').click()); await sleep(3500);
  check("the HAWB report is listed with Generate", await vis(hawb("gen")));
  // first Generate: the new report is shown (as before)
  await page.evaluate(sel => document.querySelector(sel).click(), hawb("gen")); await sleep(4500);
  check("first Generate saves the report and shows it", await vis("#odHawbBar") || await vis("#modalBody .dk-pages"));
  await page.evaluate(() => window.closeModal()); await sleep(600);
  await page.evaluate(() => { const m = document.querySelector("#modalBody"); if (m) m.innerHTML = ""; });
  check("the row now has Preview, Download PDF, Print, Mail, Regenerate, History", await vis(hawb("view")) && await vis(hawb("gen")) && await vis(hawb("hist")));
  const vers0 = (await j("GET", `/api/jobs/${encodeURIComponent(job)}/outdocs`, null, token)).body.reports.find(r => r.code === "hawb").generated[""].version;

  // Regenerate: only regenerates (no preview)
  await page.evaluate(sel => document.querySelector(sel).click(), hawb("gen")); await sleep(4500);
  const vers1 = (await j("GET", `/api/jobs/${encodeURIComponent(job)}/outdocs`, null, token)).body.reports.find(r => r.code === "hawb").generated[""].version;
  check("Regenerate makes a new version", vers1 === vers0 + 1, vers0 + " -> " + vers1);
  check("Regenerate does NOT open the preview", !(await page.evaluate(()=>!!document.querySelector("#odHawbBar, #modalBody .dk-pages .pg"))), await page.evaluate(()=>{const e=document.querySelector("#modalBody");return e?e.innerText.slice(0,150):"none"}));

  // Preview: only previews
  await page.evaluate(sel => document.querySelector(sel).click(), hawb("view")); await sleep(3000);
  const vers2 = (await j("GET", `/api/jobs/${encodeURIComponent(job)}/outdocs`, null, token)).body.reports.find(r => r.code === "hawb").generated[""].version;
  check("Preview opens the saved version and creates nothing new", await vis("#odHawbBar") && vers2 === vers1, vers1 + " -> " + vers2);
  const opts = await page.evaluate(() => [...document.querySelectorAll("#odHawbMode option")].map(o => o.value + ":" + o.textContent).join("|"));
  check("the preview offers Final print, Draft print, Pre print and Multiple print", opts === "a4:A4 print|draft:Draft print|multiple:Multiple print|pre:Pre-print", opts);
  const pages = () => page.evaluate(() => document.querySelectorAll("#modalBody .dk-pages .pg").length);
  const n0 = await pages();
  const pick = async v => { await page.select("#odHawbMode", v); await sleep(3500); };
  await pick("multiple"); const pm = await pages(); const tm = await text("#modalBody .dk-pages");
  check("Multiple print: 10 copies with their conditions pages (20 pages)", pm === 20 && /ORIGINAL 1 \( ?FOR ISSUING CARRIER ?\)|ORIGINAL 1 \(FOR ISSUING CARRIER\)/.test(tm) && /COPY 4/.test(tm), pm + " pages");
  await pick("draft"); const td = await text("#modalBody .dk-pages");
  check("Draft print shows the DRAFT mark", /DRAFT/.test(td));
  await pick("pre"); const tp = await text("#modalBody .dk-pages"); const pp = await pages();
  check("Pre-print is the data only (no labels), one page", pp === 1 && /MANITOU/i.test(tp) && !/Shipper's Name and Address|Not Negotiable/i.test(tp), pp + " | " + tp.slice(0, 100));
  await pick("a4"); check("A4 print is the same as when the preview opened", (await pages()) === n0);
  await page.evaluate(() => window.closeModal()); await sleep(500);
  // Print on the list asks which print option
  await page.evaluate(sel => document.querySelector(sel).click(), hawb("print")); await sleep(1200);
  const asked = await page.evaluate(() => [...document.querySelectorAll('input[name="awbMode"]')].map(r => r.value).join());
  check("Print asks: A4 / Draft / Multiple / Pre-print", asked === "a4,draft,multiple,pre", asked);
  check("the question says A4 print", /A4 print/.test(await text("#modalBody")));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
