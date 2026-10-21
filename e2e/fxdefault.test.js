// Headless UI check: choosing USD on a job charge, an invoice line or a booking cost line fills the Currency Master's exchange rate (editable afterwards).
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "FX" + tag.slice(-5).toUpperCase(), name: "Fx " + tag, admin_email: `fx${tag}@t.test`, admin_username: "fx" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "F" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fx" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cur = await j("POST", "/api/masters/currency", { code: "USD", name: "US Dollar", data: { currency_symbol: "$", iso_numeric_code: "840", decimal_places: "2", exchange_rate: "83.25", default_currency: "No" } }, token);
  check("setup: USD in the Currency Master with a rate", cur.status === 201, JSON.stringify(cur.body).slice(0, 150));
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s);
  await sleep(3500); w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt5"]').click(); await sleep(800);
  q("#jc_addRowBtn").click(); await sleep(800);
  const sel = q('[data-jc-f="sell.ccy"]'); sel.value = "USD"; sel.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("job cost: USD on the sell side fills the master rate", q('[data-jc-f="sell.exch"]').value === "83.25", q('[data-jc-f="sell.exch"]').value);
  const bsel = q('[data-jc-f="buy.ccy"]'); bsel.value = "USD"; bsel.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("and on the buy side", q('[data-jc-f="buy.exch"]').value === "83.25", q('[data-jc-f="buy.exch"]').value);
  const ex = q('[data-jc-f="sell.exch"]'); ex.value = "84"; ex.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("the user can still change it", q('[data-jc-f="sell.exch"]').value === "84", q('[data-jc-f="sell.exch"]').value);
  q('[data-jc-f="sell.ccy"]').value = "INR"; q('[data-jc-f="sell.ccy"]').dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("INR goes back to 1", q('[data-jc-f="sell.exch"]').value === "1");
  w.BK.openNew("invoice", { party_name: "X", lines: [{ charge_name: "A", description: "", hsn: "9967", uom: "Per Shipment", qty: "1", ccy: "INR", exch: "1", rate: "10", tax_type: "Non-Taxable", tax_pct: "0" }] }); await sleep(3500);
  const ci = q('#s-invoice-new select[data-f="ccy"]'); ci.value = "USD"; ci.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(500);
  check("invoice line: USD fills the master rate", q('#s-invoice-new input[data-f="exch"]').value === "83.25", q('#s-invoice-new input[data-f="exch"]') && q('#s-invoice-new input[data-f="exch"]').value);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
