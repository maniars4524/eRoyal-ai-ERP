// Real Chrome: the Quotation / Invoice / Job lists can be put in number order, filtered by date, month, company and status, and the columns chosen (show / hide, up / down, Apply). Saves screenshots.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const SHOTS = "../scratch"; fs.mkdirSync(SHOTS, { recursive: true });
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "LK" + tag.slice(-6).toUpperCase(), name: "ECM LOGISTICS PVT LTD", admin_email: `lk${tag}@t.test`, admin_username: "lk" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "lk" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const parties = ["SUN PHARMACEUTICAL", "ECM LOGISTICS PVT LTD", "NATURAL REMEDIES PVT LTD", "BELL MATCH COMPANY"];
  const dates = ["2026-10-29", "2026-10-23", "2026-10-22", "2026-10-16", "2026-10-15", "2026-09-14", "2026-09-02", "2026-08-20"];
  for (let i = 0; i < dates.length; i++) {
    const q = await j("POST", "/api/quotations", { party_name: parties[i % 4], mode: i % 3 === 0 ? "Air" : "Sea", direction: "Export", shipment_type: "FCL", pol: "INNSA", pod: "AEJEA", quote_date: dates[i], valid_until: "2026-11-30", lines: [{ side: "SELL", charge_name: "Documentation", ccy: "INR", exch: "1", rate: String(1000 * (i + 1)), qty: "1", basis: "Shipment", taxable: false }] }, token);
    if (q.status !== 201) console.log("seed quotation", q.status, JSON.stringify(q.body).slice(0, 120));
    await j("POST", "/api/documents", { doc_type: "invoice", party_name: parties[(i + 1) % 4], doc_date: dates[(i * 3) % dates.length], lines: [{ charge_name: "Freight", rate: String(500 * (i + 2)), tax_pct: "18" }] }, token);
    await j("POST", "/api/shipments", { mode: i % 2 ? "Sea" : "Air", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", etd: dates[i] }, token);
  }
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await (await browser.createBrowserContext()).newPage(); await page.setViewport({ width: 1500, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" });
  await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token);
  await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const heads = sel => page.$$eval(sel + " thead th", ths => ths.filter(t => t.style.display !== "none").map(t => t.textContent.trim()));
  const nums = (sel, col) => page.$$eval(sel + " tbody tr", (trs, col) => trs.filter(t => t.style.display !== "none").map(t => (Array.from(t.children).find(c => c.dataset.lkI === String(col)) || {}).textContent || ""), col);
  // ---------- quotations ----------
  await page.evaluate(() => window.go("quotation")); await sleep(2500);
  check("the quotation list has the order / filter bar and a Columns button", !!(await page.$('.lk-bar[data-lk="quotation"] [data-lk-cols]')) && !!(await page.$('.lk-bar[data-lk="quotation"] [data-lk="month"]')));
  let n = (await nums("#s-quotation", 0)).map(x => x.trim());
  const sortedDesc = n.slice().sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
  check("quotations are in number order (latest number first)", n.length >= 6 && JSON.stringify(n) === JSON.stringify(sortedDesc), n.join(" | "));
  await page.screenshot({ path: SHOTS + "/listkit-1-quotation-list.png" });
  await page.select('.lk-bar[data-lk="quotation"] [data-lk="dir"]', "asc"); await sleep(300);
  n = (await nums("#s-quotation", 0)).map(x => x.trim());
  check("the direction can be changed to lowest number first", JSON.stringify(n) === JSON.stringify(sortedDesc.slice().reverse()), n.join(" | "));
  await page.select('.lk-bar[data-lk="quotation"] [data-lk="sortBy"]', "date"); await page.select('.lk-bar[data-lk="quotation"] [data-lk="dir"]', "desc"); await sleep(300);
  const dts = (await nums("#s-quotation", 1)).map(x => Date.parse(x.trim()));
  check("the list can be ordered by date", dts.every((d, i) => i === 0 || d <= dts[i - 1]), dts.join());
  await page.$eval('.lk-bar[data-lk="quotation"] [data-lk="month"]', el => { el.value = "2026-09"; el.dispatchEvent(new Event("input", { bubbles: true })); }); await sleep(900);
  const sep = await nums("#s-quotation", 1); const visible = sep.length;
  check("the month filter shows only that month's quotations", visible === 2 && sep.every(x => /Sep/.test(x)), sep.join(" | "));
  await page.$eval('.lk-bar[data-lk="quotation"] [data-lk="month"]', el => { el.value = ""; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.select('.lk-bar[data-lk="quotation"] [data-lk="company"]', "SUN PHARMACEUTICAL"); await sleep(900);
  const sun = await page.$$eval("#s-quotation tbody tr", trs => trs.filter(t => t.style.display !== "none").map(t => t.textContent));
  check("the company filter shows only that company's quotations", sun.length === 2 && sun.every(x => /SUN PHARMACEUTICAL/.test(x)), sun.length);
  await page.screenshot({ path: SHOTS + "/listkit-2-quotation-filtered.png" });
  await page.click('.lk-bar[data-lk="quotation"] [data-lk-clear]'); await sleep(300);
  // column chooser
  await page.click('.lk-bar[data-lk="quotation"] [data-lk-cols]'); await sleep(500);
  await page.screenshot({ path: SHOTS + "/listkit-3-quotation-columns-panel.png" });
  const rowsInPanel = await page.$$eval("#lkColsBody .lk-row label", ls => ls.map(l => l.textContent.trim()));
  check("the column panel lists every column", ["QUOTE NO", "DATE", "CLIENT", "MODE", "ROUTE", "VALID UNTIL", "QUOTED AMOUNT", "STATUS / APPROVAL"].every(x => rowsInPanel.some(r => r.toUpperCase() === x)), rowsInPanel.join());
  const idx = name => rowsInPanel.findIndex(r => r.toUpperCase() === name);
  await page.click(`#lkc${idx("MODE")}`); await sleep(150);                                // hide Mode
  const ci = idx("CLIENT"); for (let k = 0; k < ci; k++) { await page.click(`[data-lk-up="${ci - k}"]`); await sleep(80); }          // Client to the first place
  await page.screenshot({ path: SHOTS + "/listkit-3b-quotation-columns-edited.png" });
  await page.click("#lkApply"); await sleep(600);
  let h = await heads("#s-quotation");
  check("Apply: Client is the first column, Mode is hidden, the others keep their order", h[0].toUpperCase() === "CLIENT" && !h.map(x => x.toUpperCase()).includes("MODE") && h.map(x => x.toUpperCase()).indexOf("QUOTE NO") === 1, h.join(" | "));
  await page.screenshot({ path: SHOTS + "/listkit-4-quotation-custom-columns.png" });
  await page.evaluate(() => window.go("dashboard")); await sleep(500); await page.evaluate(() => window.go("quotation")); await sleep(2000);
  h = await heads("#s-quotation");
  check("the chosen columns are remembered when the list is opened again", h[0].toUpperCase() === "CLIENT" && !h.map(x => x.toUpperCase()).includes("MODE"), h.join(" | "));
  const stillOpens = await page.$("#s-quotation [data-qk-open]"); check("opening a quotation still works from the rearranged list", !!stillOpens);
  // ---------- invoices ----------
  await page.evaluate(() => window.go("invoice-list")); await sleep(2500);
  check("the invoice list has the same bar", !!(await page.$('.lk-bar[data-lk="invoice"] [data-lk-cols]')));
  await page.screenshot({ path: SHOTS + "/listkit-5-invoice-list.png" });
  await page.click('.lk-bar[data-lk="invoice"] [data-lk-cols]'); await sleep(400);
  const invCols = await page.$$eval("#lkColsBody .lk-row label", ls => ls.map(l => l.textContent.trim()));
  check("the invoice columns include Proforma No and Job(s)", invCols.includes("Proforma No") && invCols.includes("Job(s)"), invCols.join());
  await page.click("#lkApply"); await sleep(300);
  // ---------- jobs ----------
  await page.evaluate(() => window.go("job-list")); await sleep(2500);
  check("the job list has the same bar", !!(await page.$('.lk-bar[data-lk="job"] [data-lk-cols]')));
  await page.screenshot({ path: SHOTS + "/listkit-6-job-list.png" });
  await browser.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
