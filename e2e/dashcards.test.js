// Headless UI check: the Jobs/Shipments style summary cards (icon, figure, drill-down) on quotation, booking, lead, enquiry, opportunity, engagement and the service screens.
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
  const org = await j("POST", "/api/organizations", { code: "DC" + tag.slice(-5).toUpperCase(), name: "Dashcards " + tag, admin_email: `dc${tag}@t.test`, admin_username: "dc" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Dc" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "dc" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const lead = (n, company, status, priority, enq) => j("PUT", "/api/crm/lead", { ref: "LD-2026-" + n, version: 0, customer_id: null, company, status, data: { id: "LD-2026-" + n, company, contact: "C", mobile: "1", email: "", source: "Website", priority, assignedTo: "Admin", status, notes: "", enquiryId: enq || null, timeline: [] } }, token);
  await lead(901, "Alpha Exports", "New", "High"); await lead(902, "Beta Imports", "New", "Low"); await lead(903, "Gamma Traders", "Lost", "Low"); await lead(904, "Delta Co", "Converted", "Medium", "ENQ-2026-0901");
  const enq = (n, company, status) => j("PUT", "/api/crm/enquiry", { ref: "ENQ-2026-" + n, version: 0, customer_id: null, company, status, data: { id: "ENQ-2026-" + n, company, contact: "C", shipmentType: "Sea Export FCL", department: "Sea Export", origin: "A", destination: "B", status, timeline: [] } }, token);
  await enq(901, "Delta Co", "New"); await enq(902, "Epsilon", "Confirmed"); await enq(903, "Zeta", "Rate Requested"); await enq(904, "Eta", "Lost");
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  const cards = id => qa(`#${id} .dash-cards[data-dash] .kpi`).map(k => [k.querySelector(".kpi-lbl").textContent, Number(k.querySelector(".kpi-val").textContent)]);
  const shown = sel => qa(sel).filter(tr => tr.style.display !== "none" && !tr.querySelector("td[colspan]")).length;
  const open = async (scr, id) => { w.go(scr); await sleep(1800); };

  await open("crm-lead", "s-crm-lead");
  let c = cards("s-crm-lead");
  check("Lead screen: summary cards with icons (total, open, converted, high priority, lost)", c.length === 5 && qa("#s-crm-lead .dash-cards svg").length >= 5, JSON.stringify(c));
  check("Lead cards count the real leads: 4 total, 3 open, 1 converted, 1 high priority, 1 lost", c[0][1] === 4 && c[1][1] === 3 && c[2][1] === 1 && c[3][1] === 1 && c[4][1] === 1, JSON.stringify(c));
  qa("#s-crm-lead .dash-cards .kpi")[4].click(); await sleep(300);
  check("clicking Lost shows only the lost lead", shown("#crmLeadRoot tbody tr") === 1);
  qa("#s-crm-lead .dash-cards .kpi")[0].click(); await sleep(300);
  check("the first card shows everything again", shown("#crmLeadRoot tbody tr") === 4);

  await open("crm-enquiry", "s-crm-enquiry");
  c = cards("s-crm-enquiry");
  check("Enquiry cards: 4 total, 1 new, 1 in pricing, 1 confirmed, 1 lost (status read from the status drop-down)", c[0][1] === 4 && c[1][1] === 1 && c[2][1] === 1 && c[3][1] === 1 && c[5][1] === 1, JSON.stringify(c));
  qa("#s-crm-enquiry .dash-cards .kpi")[3].click(); await sleep(300);
  check("clicking Confirmed shows only that enquiry", shown("#crmEnquiryRoot tbody tr") === 1);

  await open("crm-opportunity", "s-crm-opportunity");
  check("Opportunity screen has its cards", cards("s-crm-opportunity").length === 6, JSON.stringify(cards("s-crm-opportunity")));
  await open("crm-engagement", "s-crm-engagement");
  check("Engagement snapshot uses the same card style", qa("#s-crm-engagement .dash-cards .kpi-rich").length === 8);
  await open("quotation", "s-quotation");
  c = cards("s-quotation");
  check("Quotation screen: 7 status cards replace the static zeros", c.length === 7 && c[0][0] === "TOTAL QUOTATIONS", JSON.stringify(c));
  await open("booking", "s-booking");
  check("Booking screen: 6 status cards", cards("s-booking").length === 6, JSON.stringify(cards("s-booking")));
  for (const [scr, id, n] of [["svc-console", "s-svc-console", 6], ["svc-cha", "s-svc-cha", 5], ["svc-transport", "s-svc-transport", 5], ["svc-warehouse", "s-svc-warehouse", 5], ["customs", "s-customs", 5], ["transport", "s-transport", 4], ["warehouse", "s-warehouse", 3]]) {
    await open(scr, id);
    check(`${scr}: ${n} cards (all zero until there is data)`, cards(id).length === n && cards(id).every(x => x[1] === 0), JSON.stringify(cards(id)));
  }
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
