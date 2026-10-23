// Single-entry shipment journey: CRM opportunity -> ONE booking -> confirm -> freight job + customs job (same shipment) -> Shipment Console / CHA screens show the real jobs.
// Repeated clicks, a refresh and a retry never create a second booking or shipment.
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
  const org = await j("POST", "/api/organizations", { code: "JY" + tag.slice(-5).toUpperCase(), name: "Journey " + tag, admin_email: `jy${tag}@t.test`, admin_username: "jy" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Jy" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jy" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = await j("POST", "/api/customers", { code: "JYC" + tag.slice(-4), name: "Journey Exports " + tag, roles: ["shipper"], data: {} }, token);
  const company = "Journey Exports " + tag;
  const enq = { id: "ENQ-2026-0950", company, contact: "C", shipmentType: "Sea Export FCL", modeOfTransport: "Sea", department: "Sea Export", origin: "Chennai", destination: "Antwerp", pol: "INMAA", pod: "BEANR", commodity: "Soda ash", status: "Confirmed", timeline: [] };
  await j("PUT", "/api/crm/enquiry", { ref: enq.id, version: 0, customer_id: null, company, status: "Confirmed", data: enq }, token);
  const opp = { id: "OPP-2026-0950", enquiryId: enq.id, company, services: { Freight: true, CHA: true, Transport: false, Warehouse: false }, quotations: [{ id: "QT-J1", status: "Approved", total: 1000 }], charges: {}, stage: "Quotation Approved", currency: "INR", value: 1000, timeline: [], bookingId: null };
  await j("PUT", "/api/crm/opportunity", { ref: opp.id, version: 0, customer_id: null, company, status: "Open", data: opp }, token);
  const open = async () => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
    await sleep(6000); return dom.window;
  };
  let w = await open(); const q = s => w.document.querySelector(s), qa = s => Array.from(w.document.querySelectorAll(s));
  w.go("crm-opportunity"); await sleep(2500);
  check("the opportunity is loaded from the CRM", !!w.CRM_STORE.opportunity.find(o => o.id === opp.id));
  // a double click: two conversions started together
  const btn = q(`[data-crm-make-booking="${opp.id}"]`); check("the approved opportunity offers Convert to Booking", !!btn); btn.click(); btn.click(); await sleep(3500);
  let books = (await j("GET", "/api/bookings", null, token)).body;
  check("two quick clicks create exactly ONE booking in the database", books.length === 1, JSON.stringify(books.map(b => b.id)));
  const b0 = books[0];
  check("the booking carries the shipment's details: mode, direction, FCL, route, customer name and services", b0 && b0.mode === "Sea" && b0.direction === "Export" && b0.shipment_type === "FCL" && b0.pol === "INMAA" && b0.pod === "BEANR" && b0.party_name === company && /Freight, CHA/.test(b0.data.services), JSON.stringify(b0 && [b0.mode, b0.pol, b0.pod, b0.party_name, b0.data]));
  check("the organization from the master is linked (customer id), no customer is invented", b0 && b0.customer_id === cust.body.id);
  check("the CRM opportunity points at that booking and the Booking form opens for it", w.CRM_STORE.opportunity.find(o => o.id === opp.id).bookingId && w.document.getElementById("bookFormWrap") && w.document.getElementById("s-booking-new").classList.contains("on"));
  w.go("crm-opportunity"); await sleep(1500);
  check("once booked the Convert button is gone, so a retry is impossible", !q(`[data-crm-make-booking="${opp.id}"]`));
  check("a retry after it is booked creates nothing", (await j("GET", "/api/bookings", null, token)).body.length === 1);
  await sleep(5000); w.close();
  // a refresh: the booking link survives and the conversion cannot be repeated
  w = await open(); w.go("crm-opportunity"); await sleep(2500);
  const o2 = w.CRM_STORE.opportunity.find(o => o.id === opp.id);
  check("after a refresh the opportunity still knows its booking", !!(o2 && o2.bookingId && o2.stage === "Booked"), JSON.stringify(o2 && [o2.bookingId, o2.stage]));
  check("and after a refresh the Convert button is not offered again", !q(`[data-crm-make-booking="${opp.id}"]`));
  check("and a refresh plus retry still creates no second booking", (await j("GET", "/api/bookings", null, token)).body.length === 1);
  // complete the booking and confirm: the shipment gets its jobs
  await j("PATCH", `/api/bookings/${b0.id}`, { carrier: "MSC", etd: "2030-01-10", eta: "2030-02-05", si_cutoff: "2030-01-05", vgm_cutoff: "2030-01-05" }, token);
  const conf = await j("POST", `/api/bookings/${b0.id}/confirm`, null, token);
  check("confirming the booking creates the freight job and the customs job (one shipment)", conf.status === 200 && conf.body.created_jobs.length === 2, JSON.stringify(conf.body && conf.body.created_jobs));
  check("confirming twice is refused", (await j("POST", `/api/bookings/${b0.id}/confirm`, null, token)).status === 409);
  const jobs = (await j("GET", "/api/shipments", null, token)).body;
  check("the database holds exactly two jobs for the shipment, no extra shipment", jobs.length === 2 && jobs.every(x => x.booking_id === b0.id), JSON.stringify(jobs.map(x => [x.job_number, x.job_type])));
  const freight = jobs.find(x => x.job_type === "Forwarding"), cha = jobs.find(x => x.job_type === "Customs");
  w.go("svc-console"); await sleep(2500);
  const rows = qa("#svcConsoleRoot tbody tr").filter(r => !r.querySelector("td[colspan]"));
  check("Shipment Console shows ONE shipment row (the freight job) with its CHA service, from the database", rows.length === 1 && rows[0].textContent.includes(freight.job_number) && /CHA/.test(rows[0].textContent) && rows[0].textContent.includes(company), rows.map(r => r.textContent.slice(0, 120)).join("|"));
  w.go("svc-cha"); await sleep(2500);
  const crow = qa("#svcChaRoot tbody tr").filter(r => !r.querySelector("td[colspan]"));
  check("CHA screen lists the customs job linked to the same freight job", crow.length === 1 && crow[0].textContent.includes(cha.job_number) && crow[0].textContent.includes(freight.job_number), crow.map(r => r.textContent.slice(0, 120)).join("|"));
  check("its summary cards count the real job", Array.from(w.document.querySelectorAll("#s-svc-cha .dash-cards[data-dash] .kpi-val")).map(x => x.textContent).slice(0, 2).join() === "1,0");
  q('[data-svc-job="' + cha.job_number + '"]').click(); await sleep(2500);
  check("Open job goes to that job's page", w.document.getElementById("s-job-detail").classList.contains("on") && /Customs|CHA|SEA|AEX|CUS/i.test(w.document.getElementById("s-job-detail").textContent) );
  w.go("svc-transport"); await sleep(1500);
  check("Transport and Warehouse screens say there is nothing yet, instead of inventing jobs", /No transport jobs yet/.test(w.document.getElementById("svcTransportRoot").textContent));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
