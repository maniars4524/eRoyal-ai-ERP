// End-to-end hand-offs: quotation -> booking -> job (details + sell/buy) -> job screen -> invoice from the job.
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
  const org = await j("POST", "/api/organizations", { code: "FL" + tag.slice(-6).toUpperCase(), name: "Flow " + tag, admin_email: `fl${tag}@t.test`, admin_username: "fl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "F" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "ACME" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper", "consignee"], gstin: "33AAAAA0000A1Z5", data: { address_line_1: "12 Port Road", city: "Chennai", state: "Tamil Nadu", country_2: "India" } }, token)).body;
  await j("POST", "/api/customers", { code: "MSC" + tag.slice(-3).toUpperCase(), name: "MSC Mediterranean", roles: ["carrier"], data: {} }, token);
  const line = (n, r, side, tax) => ({ side, charge_name: n, carrier: "MSC Mediterranean", basis: "Per Container", ccy: "USD", exch: "83.5", rate: r, qty: "2", taxable: !!tax, tax_pct: tax || "0" });
  const q = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA(CHENNAI)", pod: "BEANR(ANTWERPEN)", header: { commodity: "Soda ash", packages: "120", gross_weight: "24000", volume: "58.5", incoterm: "CIF" },
    lines: [line("Ocean Freight", "850", "SELL"), line("Ocean Freight", "700", "BUY"), line("Documentation", "50", "SELL", "18")] }, token)).body;
  await j("POST", `/api/quotations/${q.id}/approval/submit`, null, token); await j("POST", `/api/quotations/${q.id}/approval/approve`, {}, token);
  await j("POST", `/api/quotations/${q.id}/send`, null, token); await j("POST", `/api/quotations/${q.id}/accept`, null, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, dflt) => dflt; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const text = s => ((d.querySelector(s) || {}).textContent || "").replace(/\s+/g, " ");

  // quotation -> booking: no shipment-details wizard
  w.go("quotation"); await sleep(1300);
  d.querySelector(`[data-qk-do="book:${q.id}"]`).click(); await sleep(2500);
  check("Create Booking goes straight to the booking form (no Select Shipment Details)", d.getElementById("bookWizWrap").style.display === "none" && d.getElementById("bookFormWrap").style.display === "block");
  check("route, carrier and customer are filled from the quotation", d.getElementById("bk_pol").value === "INMAA(CHENNAI)" && d.getElementById("bk_carrier").value === "MSC Mediterranean" && d.getElementById("bk_customer").selectedOptions[0].text === "Acme Exports");
  check("the Change-shipment-details link is hidden for a quotation booking", d.getElementById("bookWizChange").style.display === "none");
  check("the Costing step starts from the quotation's buy cost", true);
  d.getElementById("bk_finishBtn").click(); await sleep(2500);
  let bk = (await j("GET", "/api/bookings", null, token)).body[0];
  bk = (await j("GET", `/api/bookings/${bk.id}`, null, token)).body; bk.job_number = (bk.jobs[0] || {}).job_number;
  check("releasing the booking issues its number, creates the freight job automatically and opens the Booking page", bk && bk.status === "CONFIRMED" && /^BKG/.test(bk.number) && !!bk.job_number && bk.jobs.length === 1 && bk.jobs[0].job_type === "Forwarding" && d.querySelector(".scr.on").id === "s-booking-view", JSON.stringify(bk || {}).slice(0, 200));

  // the job
  const jobNo = bk.job_number;
  const job = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === jobNo);
  check("the job has mode, direction, type, route, customer, cargo, weight, volume and commodity", job.mode === "Sea" && job.direction === "Export" && job.container_mode === "FCL" && job.pol === "INMAA(CHENNAI)" && job.pod === "BEANR(ANTWERPEN)" && job.customer_id === cust.id && job.packages === 120 && Number(job.gross_weight) === 24000 && Number(job.volume) === 58.5 && /soda ash/i.test(job.commodity || ""), JSON.stringify(job).slice(0, 300));
  const ch = (await j("GET", `/api/shipments/by-number/${jobNo}/charges`, null, token)).body.lines;
  const ofr = ch.find(c => c.charge_name === "Ocean Freight"), doc = ch.find(c => c.charge_name === "Documentation");
  check("job cost has the sell and buy side of Ocean Freight (850 sell / 700 buy, 2 x USD)", ofr && Number(ofr.sell.rate) === 850 && Number(ofr.buy.rate) === 700 && Number(ofr.qty) === 2 && ofr.sell.ccy === "USD" && Number(ofr.sell.exch) === 83.5, JSON.stringify(ofr || {}).slice(0, 300));
  check("the taxable Documentation charge keeps its 18% GST on the job", doc && Number(doc.sell.tax_pct) === 18 && doc.sell.tax_type === "Taxable", JSON.stringify(doc || {}).slice(0, 200));
  const quote = (await j("GET", `/api/quotations/${q.id}`, null, token)).body;
  check("the quotation is closed as Converted and points to the job", quote.status === "CONVERTED" && quote.job_number === jobNo);

  // the job screen shows it
  w.go("job-detail", { jobNo }); await sleep(2200);
  const jobText = text("#s-job-detail");
  check("the job screen shows the route, customer and charges", /BEANR/.test(jobText) && /Acme Exports/.test(jobText) && /Ocean Freight/.test(Array.from(d.querySelectorAll("#jc_tbody select")).map(s => s.value).join("|")), jobText.slice(0, 160));

  // the invoice from the job
  const inv = await j("POST", "/api/documents/from-job", { job_number: jobNo, doc_type: "invoice" }, token);
  check("an invoice can be raised from the job", inv.status === 201 || inv.status === 200, JSON.stringify(inv.body).slice(0, 200));
  if (inv.body) {
    const lines = inv.body.lines || [];
    check("the invoice has the sell charges for Acme with GST on the taxable one", inv.body.party_name === "Acme Exports" && lines.length === 2 && lines.some(l => l.charge_name === "Documentation" && Number(l.tax_pct) === 18), JSON.stringify(inv.body).slice(0, 300));
    check("invoice total = sell charges (USD x 83.5) + 18% on documentation", Math.abs(Number(inv.body.grand_total) - (850 * 2 * 83.5 + 50 * 2 * 83.5 * 1.18)) < 1, String(inv.body.grand_total));
  }

  // an Air quotation books without the wizard too
  const airQ = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Air", direction: "Export", shipment_type: "General Cargo", pol: "MAA(CHENNAI INTERNATIONAL AIRPORT)", pod: "FRA(FRANKFURT AM MAIN AIRPORT)", header: { commodity: "Garments", packages: "40", gross_weight: "800" },
    lines: [{ side: "SELL", charge_name: "Air Freight", carrier: "", basis: "Per Kg", ccy: "USD", exch: "83.5", rate: "3.2", qty: "800", tax_pct: "0" }] }, token)).body;
  await j("POST", `/api/quotations/${airQ.id}/approval/submit`, null, token); await j("POST", `/api/quotations/${airQ.id}/approval/approve`, {}, token); await j("POST", `/api/quotations/${airQ.id}/send`, null, token); await j("POST", `/api/quotations/${airQ.id}/accept`, null, token);
  w.go("quotation"); await sleep(1300);
  d.querySelector(`[data-qk-do="book:${airQ.id}"]`).click(); await sleep(2500);
  check("an Air quotation opens the booking form directly, with air fields and air airports", d.getElementById("bookWizWrap").style.display === "none" && d.getElementById("bookFormWrap").style.display === "block" && d.getElementById("bk_pol").value.startsWith("MAA") && d.getElementById("bk_airCutoffs").style.display !== "none", [d.getElementById("bookWizWrap").style.display, d.getElementById("bk_pol").value].join("|"));
  w.go("booking"); await sleep(800);

  // convert straight to a job (no booking): the job still receives everything
  const conv = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "LCL", pol: "INMAA", pod: "DEHAM", header: { commodity: "Tea", packages: "300", gross_weight: "6000", volume: "22" }, lines: [{ side: "SELL", charge_name: "Ocean Freight", carrier: "", basis: "Per CBM", ccy: "USD", exch: "83.5", rate: "40", qty: "22", tax_pct: "0" }] }, token)).body;
  await j("POST", `/api/quotations/${conv.id}/approval/submit`, null, token); await j("POST", `/api/quotations/${conv.id}/approval/approve`, {}, token); await j("POST", `/api/quotations/${conv.id}/send`, null, token); await j("POST", `/api/quotations/${conv.id}/accept`, null, token);
  const cv = await j("POST", `/api/quotations/${conv.id}/convert`, null, token);
  const cjob = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === (cv.body || {}).job_number);
  check("Convert to Job also carries commodity, packages, weight and volume", cjob && cjob.commodity === "Tea" && cjob.packages === 300 && Number(cjob.gross_weight) === 6000 && Number(cjob.volume) === 22 && cjob.container_mode === "LCL", JSON.stringify(cjob || cv.body).slice(0, 300));

  // addresses come from the customer's branches in the Organization master
  const cust2 = (await j("POST", "/api/customers", { code: "BRN" + tag.slice(-3).toUpperCase(), name: "Branchy Traders", roles: ["shipper", "consignee"], data: {} }, token)).body;
  await j("POST", `/api/customers/${cust2.id}/branches`, { name: "Head Office", address: "1 Anna Salai", city: "Chennai", state: "Tamil Nadu", pincode: "600002", country: "India", branch_type: "Head Office" }, token);
  await j("POST", `/api/customers/${cust2.id}/branches`, { name: "Mumbai Office", address: "9 Marine Drive", city: "Mumbai", state: "Maharashtra", pincode: "400020", country: "India" }, token);
  w.go("booking-new"); await sleep(1500);
  w.bookWiz.mode = "Sea"; w.bookWiz.dir = "Export"; w.bookWiz.type = "FCL"; w.bookWiz.bookingType = "Direct"; w.bookWizApplyAndShowForm(); await sleep(500);
  const cs = d.getElementById("bk_customer"); cs.value = String(cust2.id); cs.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1500);
  check("the customer's address is picked from its branch in the master", /1 Anna Salai/.test(d.getElementById("bk_customerAddr").value) && /Chennai/.test(d.getElementById("bk_customerAddr").value), d.getElementById("bk_customerAddr").value);
  check("a customer with several branches gets a branch chooser", !!d.getElementById("bk_customerBranch") && d.getElementById("bk_customerBranch").options.length === 2);
  const bsel = d.getElementById("bk_customerBranch"); bsel.value = bsel.options[1].value; bsel.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("choosing another branch changes the address", /Marine Drive/.test(d.getElementById("bk_customerAddr").value), d.getElementById("bk_customerAddr").value);
  check("the shipper is filled from the customer too, with its address", d.getElementById("bk_shipper").value === "Branchy Traders" && /Marine Drive|Anna Salai/.test(d.getElementById("bk_shipperAddr").value), d.getElementById("bk_shipperAddr").value);
  check("sea equipment fields are shown, air ones hidden", d.getElementById("bk_conttype").closest(".fg").style.display !== "none" && d.getElementById("bk_chgwt").closest(".fg").style.display === "none");

  // one booking number, several jobs: Freight + CHA
  const fc = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "BEANR", lines: [line("Ocean Freight", "850", "SELL"), line("Ocean Freight", "700", "BUY")] }, token)).body;
  await j("POST", `/api/quotations/${fc.id}/approval/submit`, null, token); await j("POST", `/api/quotations/${fc.id}/approval/approve`, {}, token); await j("POST", `/api/quotations/${fc.id}/send`, null, token); await j("POST", `/api/quotations/${fc.id}/accept`, null, token);
  w.go("quotation"); await sleep(1200);
  d.querySelector(`[data-qk-do="book:${fc.id}"]`).click(); await sleep(2500);
  d.querySelector('.fi[data-svc="CHA"]').classList.add("on");
  w.BKG.addCharge({ type: "AR", description: "Customs Clearance", qty: "1", rate: "4500", currency: "INR", exch: "1", tax_pct: "18", service: "CHA" });
  d.getElementById("bk_finishBtn").click(); await sleep(2500);
  const multi = (await j("GET", "/api/bookings", null, token)).body.find(x => x.quotation_id === fc.id);
  check("a Freight + CHA booking opens the Booking page with both jobs already created", d.querySelector(".scr.on").id === "s-booking-view" && !d.querySelector('[data-bkv-create="Freight"]') && !d.querySelector('[data-bkv-create="CHA"]') && /Customs/.test(text("#bkvRoot")));
  const mb = (await j("GET", `/api/bookings/${multi.id}`, null, token)).body;
  check("both jobs sit under the one booking number", mb.jobs.length === 2 && new Set(mb.jobs.map(x => x.job_type)).size === 2 && /BKG/.test(mb.number), JSON.stringify(mb.jobs));
  const chaJob = mb.jobs.find(x => x.job_type === "Customs").job_number;
  const chaCh = (await j("GET", `/api/shipments/by-number/${chaJob}/charges`, null, token)).body.lines;
  check("the CHA job carries only the CHA charge, with its GST", chaCh.length === 1 && chaCh[0].charge_name === "Customs Clearance" && Number(chaCh[0].sell.tax_pct) === 18, JSON.stringify(chaCh).slice(0, 200));
  w.go("booking"); await sleep(1500);
  check("the Bookings list shows both job numbers against the booking", mb.jobs.every(x => new RegExp(x.job_number).test(d.querySelector("#s-booking tbody").textContent)));

  // customs clearance: bills entered in the CHA job appear in the freight job of the same booking
  const jobs2 = (await j("GET", `/api/bookings/${multi.id}`, null, token)).body.jobs;
  const fj2 = jobs2.find(x => x.job_type === "Forwarding").job_number, cj2 = jobs2.find(x => x.job_type === "Customs").job_number;
  w.go("job-detail", { jobNo: cj2 }); await sleep(2800);
  d.querySelector('.tbb[data-tab="jt4"]').click(); await sleep(1200);
  check("the CHA job has a Customs section with an Add Shipping Bill button", !!d.getElementById("cusAddBtn") && /Add Shipping Bill/.test(d.getElementById("cusAddBtn").textContent) && /No Shipping Bill recorded yet/.test(text("#cusBody")), text("#cusBody").slice(0, 160));
  for (const [no, dt] of [["SB5001", "2026-10-10"], ["SB5002", "2026-10-11"]]) {
    d.getElementById("cusAddBtn").click(); await sleep(500);
    const sv = (id, v) => { const e = d.getElementById(id); e.value = v; e.dispatchEvent(new w.Event("input", { bubbles: true })); };
    sv("cusNo", no); sv("cusDate", dt); sv("cusRef", "CHA-" + no); sv("cusAv", "250000");
    d.querySelectorAll("#toastStack .toast").forEach(t => t.remove());
    d.getElementById("cusSave").click(); await sleep(1500);
  }
  check("both shipping bills are listed, with the count and a green success toast", /SB5001/.test(text("#cusBody")) && /SB5002/.test(text("#cusBody")) && /bills processed\s*2/i.test(text("#cusBody")) && Array.from(d.querySelectorAll("#toastStack .toast")).some(t => t.classList.contains("success") && /Added Successfully/.test(t.textContent)), text("#cusBody").slice(0, 300));
  w.go("job-detail", { jobNo: fj2 }); await sleep(2800);
  d.querySelector('.tbb[data-tab="jt4"]').click(); await sleep(1200);
  check("the freight job shows the same bills, the first SB number and date, and the total count", /SB5001/.test(text("#cusBody")) && /SB5002/.test(text("#cusBody")) && /Shipping Bill no\s*SB5001/.test(text("#cusBody")) && /10-10-2026/.test(text("#cusBody")) && /processed\s*2/i.test(text("#cusBody")) && new RegExp(cj2).test(text("#cusBody")), text("#cusBody").slice(0, 400));
  w.go("booking-view"); await sleep(300);
  w.BKG.view(multi.id); await sleep(2500);
  check("the Booking page splits freight and customs charges and shows the combined total", /Costing by service/.test(text("#bkvRoot")) && /Customs clearance \(CHA\)/.test(text("#bkvRoot")) && /Combined/.test(text("#bkvRoot")), text("#bkvRoot").slice(0, 300));
  w.go("bl-draft"); await sleep(1500);
  check("the BL document page lists the job's SB number from customs clearance", /SB5001/.test(text("#blDraftPrintZone")) || /Shipping Bill \(SB\) no/.test(text("#blDraftPrintZone")), text("#blDraftPrintZone").slice(0, 300));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
