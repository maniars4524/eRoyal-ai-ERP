// Headless UI check: in the purchase bill / invoice editor, choosing the vendor or customer fills GSTIN, place of supply and the supply type from the master, and the Job Reference lists only the jobs with unbilled entries for that party.
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
  const org = await j("POST", "/api/organizations", { code: "JP" + tag.slice(-5).toUpperCase(), name: "Jobpick " + tag, admin_email: `jp${tag}@t.test`, admin_username: "jp" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "J" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jp" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { state: "Maharashtra", state_code: "27", gstin: "27AAACS1234A1Z5" }, token);
  const shipper = (await j("POST", "/api/customers", { code: "JPS", name: "Jp Shipper", roles: ["shipper"], gstin: "27AABCU9603R1ZM", data: { state: "Maharashtra", state_code: "27" } }, token)).body;
  const vendor = (await j("POST", "/api/customers", { code: "JPV", name: "Jp Carrier", roles: ["carrier"], gstin: "33AACCM4945P1Z2", data: { state: "Tamil Nadu", state_code: "33" } }, token)).body;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", pol: "INMAA", pod: "SGSIN", customer_id: shipper.id }, token)).body.job_number;
  const job2 = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", pol: "INMAA", pod: "AEJEA", customer_id: shipper.id }, token)).body.job_number;
  const side = (rate, party) => ({ ccy: "INR", exch: "1", rate, tax_type: "Non-Taxable", tax_pct: "0", ...(party ? { party, use_party: true } : {}) });
  await j("PUT", `/api/shipments/by-number/${job}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "996511", buy: side("700", "Jp Carrier"), sell: side("900", "Jp Shipper") }] }, token);
  await j("PUT", `/api/shipments/by-number/${job2}/charges`, { lines: [{ charge_name: "THC", qty: "1", hsn: "996511", buy: side("100", "Jp Carrier"), sell: side("150", "Jp Shipper") }] }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  w.BK.openNew("purchase_invoice"); await sleep(3000);
  const scr = "#s-purchase-invoice-new ";
  const sel = () => q(scr + '[data-bkh="job_number"]');
  check("with no vendor chosen the job list asks for the vendor first", sel().tagName === "SELECT" && /Choose the party first/.test(sel().options[0].textContent));
  const pn = q(scr + '[data-bkh="party_name"]'); pn.value = "Jp Carrier"; pn.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(2000);
  check("the vendor's GSTIN comes from the master", q(scr + '[data-bkh="party_gstin"]').value === "33AACCM4945P1Z2", q(scr + '[data-bkh="party_gstin"]').value);
  check("place of supply is the company's state and the supply is inter-state (IGST)", q(scr + '[data-bkh="place_of_supply"]').value === "27-Maharashtra" && q(scr + '[data-bkh="supply_type"]').value === "inter", q(scr + '[data-bkh="place_of_supply"]').value + " / " + q(scr + '[data-bkh="supply_type"]').value);
  const opts = Array.from(sel().options).map(o => o.value).filter(Boolean);
  check("the job list shows only this vendor's jobs with unbilled entries", opts.sort().join() === [job, job2].sort().join(), opts.join());
  sel().value = job; sel().dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1800);
  const names = qa(scr + '[data-f="charge_name"]').map(x => x.value);
  check("choosing the job puts its unbilled buy charge on the bill at the buy rate", names.join() === "Ocean Freight" && Number(qa(scr + '[data-f="rate"]')[0].value) === 700 && q(scr + '[data-bkh="job_number"]').value === job, names.join() + " " + (qa(scr + '[data-f="rate"]')[0] || {}).value);
  await q(scr + '[data-bk="save"]').click(); await sleep(2000);
  const docs = (await j("GET", "/api/documents?doc_type=purchase_invoice", null, token)).body;
  check("the saved purchase bill belongs to the vendor, the job and the master's GSTIN", docs.length === 1 && docs[0].customer_id === vendor.id && docs[0].job_number === job && docs[0].party_gstin === "33AACCM4945P1Z2", JSON.stringify(docs[0]));
  const left = (await j("GET", `/api/documents/billing/billable-jobs?party=Jp Carrier&doc_type=purchase_invoice`, null, token)).body.map(x => x.job_number);
  check("that job no longer appears as unbilled for the vendor", left.join() === job2, left.join());
  // sales side
  w.BK.openNew("invoice"); await sleep(3000);
  const sc = "#s-invoice-new ";
  const pn2 = q(sc + '[data-bkh="party_name"]'); pn2.value = "Jp Shipper"; pn2.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(2000);
  check("sales invoice: GSTIN, place of supply (customer's state) and intra-state supply come from the master", q(sc + '[data-bkh="party_gstin"]').value === "27AABCU9603R1ZM" && q(sc + '[data-bkh="place_of_supply"]').value === "27-Maharashtra" && q(sc + '[data-bkh="supply_type"]').value === "intra", q(sc + '[data-bkh="party_gstin"]').value + " " + q(sc + '[data-bkh="supply_type"]').value);
  const sopts = Array.from(q(sc + '[data-bkh="job_number"]').options).map(o => o.value).filter(Boolean);
  check("sales invoice: both unbilled jobs of the customer are offered", sopts.sort().join() === [job, job2].sort().join(), sopts.join());
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
