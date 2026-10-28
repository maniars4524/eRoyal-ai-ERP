// Accounts: the invoice job picker has a Job category (All / Freight / CHA / Transport); a CHA or Transport user sees only their own.
// CHA screen: Back + Create Job (Export / Import x Sea / Air), dashboard cards (Draft, Pending, Filed, Delayed, Completed ...), register, job window with stages.
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
  const org = await j("POST", "/api/organizations", { code: "IC" + tag.slice(-6).toUpperCase(), name: "InvCat " + tag, admin_email: `ic${tag}@t.test`, admin_username: "ic" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ic" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const party = "Category Customer Ltd";
  await j("POST", "/api/customers", { code: "CATC1", name: party, roles: ["customer", "shipper"], country: "India" }, token);
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: party, carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "Freight, CHA", pcs: "10", consignee: "X Buyer" } }, token)).body;
  await j("POST", `/api/bookings/${bk.id}/confirm`, null, token);
  const cj = (await j("POST", `/api/bookings/${bk.id}/jobs`, { service: "CHA" }, token)).body.created_job;
  const fj = (await j("POST", `/api/bookings/${bk.id}/jobs`, { service: "Freight" }, token)).body.created_job;
  for (const [jn, name] of [[fj, "Ocean Freight"], [cj, "Customs Clearance"]]) await j("PUT", `/api/shipments/by-number/${jn}/charges`, { lines: [{ charge_name: name, uom: "Per Shipment", qty: "1", sell: { ccy: "INR", exch: "1", rate: "1000", tax_type: "Taxable", tax_pct: "18", party }, buy: {} }] }, token);
  const open = async () => { w.go("invoice-new"); await sleep(2500); const p = d.querySelector('#s-invoice-new [data-bkh="party_name"]'); p.value = party; p.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(2000); };
  const cats = () => Array.from(d.querySelectorAll("#s-invoice-new [data-bk-svc]")).map(x => x.dataset.bkSvc);
  const jobs = () => Array.from(d.querySelectorAll('#s-invoice-new [data-bk-jobpick] option')).map(o => o.value).filter(Boolean);
  await open();
  check("Admin sees the job categories All, Freight, CHA, Transport and Warehouse", cats().join() === "All,Freight,CHA,Transport,Warehouse", cats().join());
  check("All lists the freight job and the CHA job", jobs().includes(fj) && jobs().includes(cj), jobs().join());
  d.querySelector('#s-invoice-new [data-bk-svc="CHA"]').click(); await sleep(1800);
  check("CHA lists only the CHA job", jobs().join() === cj, jobs().join());
  d.querySelector('#s-invoice-new [data-bk-svc="Freight"]').click(); await sleep(1800);
  check("Freight lists only the freight job", jobs().join() === fj, jobs().join());
  d.querySelector('#s-invoice-new [data-bk-svc="Transport"]').click(); await sleep(1800);
  check("Transport lists no job (none exist)", jobs().length === 0);
  // a slow earlier request (the party field losing focus as the category is clicked) must not bring back the other category's jobs
  { const p = d.querySelector('#s-invoice-new [data-bkh="party_name"]'); p.dispatchEvent(new w.Event("change", { bubbles: true })); d.querySelector('#s-invoice-new [data-bk-svc="CHA"]').click(); await sleep(2500);
    check("clicking CHA right after the party field changed still lists only the CHA job", jobs().join() === cj && !jobs().includes(fj), jobs().join()); }
  d.querySelector('.hdr-menu-item[data-role="CHA"]').click(); await sleep(500);
  await open();
  check("a CHA user sees only the CHA category, with the CHA job", cats().join() === "CHA" && jobs().join() === cj, cats().join() + " / " + jobs().join());
  d.querySelector('.hdr-menu-item[data-role="Transport"]').click(); await sleep(500);
  await open();
  check("a Transport user sees only the Transport category", cats().join() === "Transport" && jobs().length === 0, cats().join());
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
