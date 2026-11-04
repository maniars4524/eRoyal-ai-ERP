// Real Chrome: on every list with a Columns chooser, the chooser, the table header and the table rows show the same columns in the same order (also after a saved custom order / hidden column).
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const norm = a => JSON.stringify(a.map(x => x.split(" / ").sort().join(" / ")));
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "CM" + tag, name: "Cols " + tag, admin_email: `cm${tag}@t.test`, admin_username: "cm" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cm" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "CM" + tag, name: "Cols cust " + tag }, token)).body;
  await j("PUT", "/api/crm/lead", { ref: "LD" + tag, customer_id: cust.id, company: cust.name, status: "New" }, token);
  await j("POST", "/api/quotations", { party_name: cust.name, customer_id: cust.id, mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "DEHAM", lines: [{ side: "SELL", charge_name: "OF", basis: "Per Container", ccy: "USD", exch: "83.5", rate: "850", qty: "2", tax_pct: "0", carrier: "" }] }, token);
  await j("POST", "/api/documents", { doc_type: "invoice", customer_id: cust.id, party_name: cust.name }, token);
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", consignee: "Cols", customer_id: cust.id }, token);
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1600, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const lists = [["crm-lead", "lead"], ["crm-enquiry", "enquiry"], ["quotation", "quotation"], ["invoice-list", "invoice"], ["job-list", "job"], ["booking", "booking"], ["purchase-invoice-list", "purchaseinv"]];
  const snap = () => page.evaluate(() => { const scr = document.querySelector(".scr.on"); const t = Array.from(scr.querySelectorAll("table")).find(x => x.tHead && x.tHead.rows[0].cells.length >= 4);
    const vis = c => c.style.display !== "none"; const heads = Array.from(t.tHead.rows[0].cells).filter(vis).map(c => c.textContent.trim()).filter(Boolean);
    const row = Array.from(t.tBodies[0].rows).find(r => r.children.length >= 4); const cells = row ? Array.from(row.children).filter(vis).length : -1;
    return { heads, headCells: Array.from(t.tHead.rows[0].cells).filter(vis).length, cells }; });
  const panel = async () => { await page.evaluate(() => document.querySelector(".scr.on [data-lk-cols]").click()); await sleep(500);
    const r = await page.evaluate(() => Array.from(document.querySelectorAll("#lkColsBody .lk-row")).map(x => ({ n: x.querySelector("label").textContent.trim(), on: x.querySelector("input").checked })));
    await page.evaluate(() => closeModal()); await sleep(200); return r; };
  for (const [s, key] of lists) {
    await page.evaluate(s => document.querySelector('[data-go="' + s + '"]').click(), s); await sleep(3000);
    let sn = await snap(), pn = await panel();
    check(key + ": default - chooser lists exactly the visible columns in order", JSON.stringify(pn.filter(x => x.on).map(x => x.n)) === JSON.stringify(sn.heads) && (sn.cells === -1 || sn.headCells === sn.cells), JSON.stringify({ pn: pn.map(x => x.n), heads: sn.heads, hc: sn.headCells, c: sn.cells }));
    // save a custom order (move the 2nd column to the end) and hide the 3rd, through the chooser itself
    await page.evaluate(() => document.querySelector(".scr.on [data-lk-cols]").click()); await sleep(500);
    await page.evaluate(() => { document.querySelector('#lkColsBody [data-lk-down="1"]').click(); }); await sleep(200);
    await page.evaluate(() => { document.querySelector('#lkColsBody [data-lkc="2"]').click(); }); await sleep(200);
    const want = await page.evaluate(() => Array.from(document.querySelectorAll("#lkColsBody .lk-row")).filter(x => x.querySelector("input").checked).map(x => x.querySelector("label").textContent.trim()));
    await page.evaluate(() => document.getElementById("lkApply").click()); await sleep(1200);
    sn = await snap();
    check(key + ": after Apply the table shows the chosen columns in the chosen order", norm(sn.heads) === norm(want) && (sn.cells === -1 || sn.headCells === sn.cells), JSON.stringify({ want, heads: sn.heads, hc: sn.headCells, c: sn.cells }));
    await page.evaluate(s => document.querySelector('[data-go="dashboard"]').click(), s); await sleep(500);
    await page.evaluate(s => document.querySelector('[data-go="' + s + '"]').click(), s); await sleep(3000);
    sn = await snap(); pn = await panel();
    check(key + ": coming back later, table and chooser still agree", JSON.stringify(pn.filter(x => x.on).map(x => x.n)) === JSON.stringify(sn.heads) && norm(sn.heads) === norm(want), JSON.stringify({ pn: pn.map(x => x.n + (x.on ? "" : "(off)")), heads: sn.heads, want }));
    await page.evaluate(() => document.querySelector(".scr.on [data-lk-cols]").click()); await sleep(500);
    await page.evaluate(() => document.getElementById("lkReset").click()); await sleep(1000);
  }
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
