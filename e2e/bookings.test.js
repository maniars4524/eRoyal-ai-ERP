// Headless UI check: quotation -> booking (prefilled, linked) -> save draft -> confirm -> job with the quotation's charges; Bookings list actions.
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
  const org = await j("POST", "/api/organizations", { code: "BK" + tag.slice(-6).toUpperCase(), name: "Book " + tag, admin_email: `bk${tag}@t.test`, admin_username: "bk" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "B" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "bk" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = await j("POST", "/api/customers", { code: "ACME" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper", "consignee"], data: { address_line_1: "12 Port Road", city: "Chennai", country_2: "India" } }, token);
  await j("POST", "/api/customers", { code: "MSC" + tag.slice(-3).toUpperCase(), name: "MSC Mediterranean", roles: ["carrier"], data: {} }, token);
  const line = (rate, side) => ({ side, charge_name: "Ocean Freight", carrier: "", basis: "Per Container", ccy: "USD", exch: "83.5", rate, qty: "2", tax_pct: "0" });
  const q = (await j("POST", "/api/quotations", { customer_id: cust.body.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA(CHENNAI)", pod: "BEANR(ANTWERPEN)", lines: [line("850", "SELL"), line("700", "BUY")] }, token)).body;
  await j("POST", `/api/quotations/${q.id}/approval/submit`, null, token);
  const ap = await j("POST", `/api/quotations/${q.id}/approval/approve`, {}, token);
  check("a quotation gets its number when approved", ap.status === 200 && /^QT\//.test(ap.body.number), JSON.stringify(ap.body).slice(0, 120));
  await j("POST", `/api/quotations/${q.id}/send`, null, token);
  await j("POST", `/api/quotations/${q.id}/accept`, null, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => (q$(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (id, v) => { const e = d.getElementById(id); e.value = v; e.dispatchEvent(new w.Event("change", { bubbles: true })); e.dispatchEvent(new w.Event("input", { bubbles: true })); };

  w.go("booking"); await sleep(1500);
  check("an empty Bookings list says so", /No bookings yet/.test(text("#s-booking tbody")));

  // the accepted quotation offers Create Booking
  w.go("quotation"); await sleep(1500);
  check("the quotation list offers Create Booking for the accepted quotation", !!q$(`[data-qk-do="book:${q.id}"]`));
  await w.BKG.fromQuotation(q.id); await sleep(1800);
  check("the booking form opens prefilled from the quotation", d.getElementById("bk_pol").value === "INMAA(CHENNAI)" && d.getElementById("bk_pod").value === "BEANR(ANTWERPEN)" && d.getElementById("bk_customer").selectedOptions[0].text === "Acme Exports", [d.getElementById("bk_pol").value, d.getElementById("bk_customer").value].join("|"));
  check("it says which quotation it comes from", /Raised from quotation QT\//.test(text("#bookFormWrap")));
  check("customer address comes from the Organization master (once the customer is picked)", (() => { setf("bk_customer", String(cust.body.id)); return /12 Port Road/.test(d.getElementById("bk_customerAddr").value); })(), d.getElementById("bk_customerAddr").value);
  check("carrier list comes from the master", Array.from(d.querySelectorAll("#bk_carrier option")).some(o => o.value === "MSC Mediterranean"));
  setf("bk_carrier", "MSC Mediterranean"); setf("bk_vessel", "MSC ROMA / 254W"); setf("bk_etd", "2030-01-10"); setf("bk_eta", "2030-02-05"); setf("bk_sicutoff", "2030-01-05"); setf("bk_shipper", "Acme Exports"); setf("bk_consignee", "Acme Exports"); setf("bk_pcs", "120"); setf("bk_gwt", "24000"); setf("bk_marks", "SODA ASH");
  w.BKG.addCharge({ type: "AP", code: "OFR", description: "Ocean freight", qty: "2", rate: "700", currency: "USD" });
  check("a costing row calculates its amount", d.querySelector('#bk_chargeRows [data-f="amount"]').value === "1400.00");
  d.getElementById("bk_saveBtn").click(); await sleep(1200);
  const drafts = (await j("GET", "/api/bookings", null, token)).body;
  check("Save Draft stores it with the quotation link, parties and charges", drafts.length === 1 && drafts[0].status === "DRAFT" && drafts[0].quotation_id === q.id && drafts[0].carrier === "MSC Mediterranean" && drafts[0].data.consignee === "Acme Exports" && drafts[0].data.charges.length === 1, JSON.stringify(drafts[0] || {}).slice(0, 300));

  d.getElementById("bk_finishBtn").click(); await sleep(2500);
  const all = (await j("GET", "/api/bookings", null, token)).body;
  check("Confirm Booking numbers it and creates the job", all[0].status === "CONFIRMED" && /^BKG/.test(all[0].number || "") && !!all[0].job_number, JSON.stringify(all[0] || {}).slice(0, 250));
  const quote = (await j("GET", `/api/quotations/${q.id}`, null, token)).body;
  check("the quotation is now Converted to that job", quote.status === "CONVERTED" && quote.job_number === all[0].job_number, quote.status);
  const ch = (await j("GET", `/api/shipments/by-number/${all[0].job_number}/charges`, null, token)).body.lines;
  check("the job carries the quotation's sell charge and buy cost", ch.some(c => Number(c.sell.rate) === 850 && Number(c.buy.rate) === 700), JSON.stringify(ch).slice(0, 200));

  w.go("booking"); await sleep(1800);
  check("the Bookings list shows the confirmed booking with job, carrier, vessel and status", /Confirmed/.test(text("#s-booking tbody")) && new RegExp(all[0].job_number).test(text("#s-booking tbody")) && /MSC ROMA/.test(text("#s-booking tbody")), text("#s-booking tbody").slice(0, 200));
  check("the KPI cards count it", Number(text("#s-booking .kpi:nth-child(1) .kpi-val")) === 1, text("#s-booking .kpi-row"));
  q$(`[data-bk-act="dup:${all[0].id}"]`).click(); await sleep(1800);
  const after = (await j("GET", "/api/bookings", null, token)).body;
  check("Duplicate opens a new draft without number, job or quotation link", after.length === 2 && after.some(b => b.status === "DRAFT" && !b.number && !b.job_number && !b.quotation_id));
  w.go("booking"); await sleep(1500);
  const draftRow = after.find(b => b.status === "DRAFT");
  q$(`[data-bk-act="delete:${draftRow.id}"]`).click(); await sleep(1200);
  check("a draft can be deleted from the list", (await j("GET", "/api/bookings", null, token)).body.length === 1);
  q$(`[data-bk-act="release:${all[0].id}"]`).click(); await sleep(1200);
  check("space can be released on a confirmed booking", (await j("GET", `/api/bookings/${all[0].id}`, null, token)).body.status === "SPACE_RELEASED");

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
