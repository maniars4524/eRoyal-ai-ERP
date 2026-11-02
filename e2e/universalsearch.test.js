// Real Chrome: the header search box shows results grouped Department > Import/Export > Service > Category, with filters, collapse, load more and open. Saves screenshots to scratch/.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
fs.mkdirSync("../scratch", { recursive: true });
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "US" + tag, name: "Search Co " + tag, admin_email: `us${tag}@t.test`, admin_username: "us" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "us" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const party = "ZQXV" + tag + " TRADERS";
  const sea = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", consignee: party }, token)).body;
  const air = (await j("POST", "/api/shipments", { mode: "Air", direction: "Import", pol: "SIN", pod: "INMAA4", consignee: party }, token)).body;
  await j("POST", "/api/shipments", { mode: "Road", direction: "Domestic", pol: "Chennai", pod: "Bangalore", consignee: party }, token);
  await j("POST", "/api/quotations", { party_name: party, mode: "Sea", direction: "Export", pol: "INMAA", pod: "BEANR", lines: [{ side: "SELL", charge_name: "Docs", ccy: "INR", exch: "1", rate: "100", qty: "1", basis: "Shipment", taxable: false }] }, token);
  await j("POST", "/api/documents", { doc_type: "invoice", party_name: party, job_number: sea.job_number, lines: [{ charge_name: "Freight", rate: "100", tax_pct: "0", job_number: sea.job_number }] }, token);
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await (await browser.createBrowserContext()).newPage(); await page.setViewport({ width: 1500, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" });
  await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token);
  await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  await page.click("#globalSearch"); await page.keyboard.type("ZQXV" + tag, { delay: 40 }); await sleep(1500);
  const text = () => page.$eval("#gsResults", e => e.textContent.replace(/\s+/g, " "));
  let t = await text();
  check("the search panel opens with the heading and the result count", /Search results for/.test(t) && /Showing \d+ of \d+ results/.test(t), t.slice(0, 160));
  check("results are grouped by department", /Logistics & Customs/.test(t) && /Accounts & Finance/.test(t) && /CRM/.test(t), t.slice(0, 300));
  check("then by Import / Export / Domestic", /IMPORT \(/i.test(t) && /EXPORT \(/i.test(t) && /DOMESTIC \(/i.test(t), t.slice(0, 400));
  check("then by service type and transaction category", /Sea Freight \u203A Jobs/.test(t) && /Air Freight \u203A Jobs/.test(t) && /Road Freight \u203A Jobs/.test(t) && /Invoices/.test(t) && /Quotations/.test(t), t.slice(0, 600));
  check("each record shows its number, type and customer", /Sea Export Job/.test(t) && t.includes(sea.job_number) && t.includes(party), "");
  await page.screenshot({ path: "../scratch/search-1-results.png" });
  // filters
  await page.select('#gsResults [data-uf="division"]', "Import"); await sleep(1200);
  t = await text();
  check("the Import filter keeps only Import records", t.includes(air.job_number) && !t.includes(sea.job_number) && !/EXPORT \(/i.test(t), t.slice(0, 200));
  await page.screenshot({ path: "../scratch/search-2-import-filter.png" });
  await page.click("#gsResults [data-uclear]"); await sleep(1200);
  await page.select('#gsResults [data-uf="mode"]', "Sea"); await sleep(1200);
  t = await text();
  check("the Sea filter hides Air and Road records", t.includes(sea.job_number) && !t.includes(air.job_number), "");
  await page.click("#gsResults [data-uclear]"); await sleep(1200);
  // collapse a department
  await page.click('#gsResults [data-udep="Accounts & Finance"]'); await sleep(300);
  check("a department can be collapsed", !/Invoices \(/.test(await text()) && /Accounts & Finance/.test(await text()));
  await page.click('#gsResults [data-udep="Accounts & Finance"]'); await sleep(300);
  // open a job from the result
  const row = await page.evaluateHandle(id => Array.from(document.querySelectorAll("#gsResults .u-row")).find(r => r.textContent.includes(id)), sea.job_number);
  await row.asElement().click(); await sleep(2500);
  check("clicking a record opens that exact job", (await page.evaluate(() => document.body.dataset.screen)) === "job-detail" && /SEAEXP|SE|AE|RD/.test(await page.evaluate(() => (document.getElementById("jobDetailNo") || {}).textContent || "")), await page.evaluate(() => document.body.dataset.screen));
  // reference format
  await page.$eval("#globalSearch", e => { e.value = ""; e.focus(); }); await page.keyboard.type(sea.job_number.replace(/-/g, "").toLowerCase(), { delay: 30 }); await sleep(1500);
  check("a reference typed without dashes and in lower case still finds the job", (await text()).includes(sea.job_number), (await text()).slice(0, 200) + " | input=" + (await page.$eval("#globalSearch", e => e.value)));
  await page.$eval("#globalSearch", e => { e.value = ""; e.focus(); }); await page.keyboard.type("NOTHINGLIKETHIS" + tag, { delay: 20 }); await sleep(1500);
  check("no result shows a clear message", /No record found/.test(await text()), await text());
  await page.keyboard.press("Escape"); await sleep(300);
  check("Escape closes the panel", !(await page.$eval("#gsResults", e => e.classList.contains("on"))));
  await browser.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
