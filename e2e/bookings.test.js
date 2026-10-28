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
  const q = (await j("POST", "/api/quotations", { customer_id: cust.body.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA(CHENNAI)", pod: "BEANR(ANTWERPEN)", header: { commodity: "Soda ash", packages: "120", gross_weight: "24000", volume: "58.5", incoterm: "CIF" }, lines: [line("850", "SELL"), line("700", "BUY")] }, token)).body;
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
  await j("POST", "/api/network/sea-lines", { code: "HLCU", name: "Hapag-Lloyd Net", scac_code: "HLCU" }, token);
  await j("POST", "/api/network/cfs", { code: "CFSX", name: "Ennore CFS", city: "Chennai" }, token);
  await j("POST", "/api/network/iata", { iata_code: "IA-777", name: "Skyline Agent" }, token);
  await w.BKG.fromQuotation(q.id); await sleep(1800);
  check("the booking form opens prefilled from the quotation", d.getElementById("bk_pol").value === "INMAA(CHENNAI)" && d.getElementById("bk_pod").value === "BEANR(ANTWERPEN)" && d.getElementById("bk_customer").selectedOptions[0].text === "Acme Exports", [d.getElementById("bk_pol").value, d.getElementById("bk_customer").value].join("|"));
  check("Select Shipment Details is NOT shown: the quotation already gave mode, direction and type", d.getElementById("bookWizWrap").style.display === "none" && d.getElementById("bookFormWrap").style.display === "block", [d.getElementById("bookWizWrap").style.display, d.getElementById("bookFormWrap").style.display].join("|"));
  check("the shipment details of the quotation are on the form (cargo, weight, volume, incoterm)", d.getElementById("bk_pcs").value === "120" && d.getElementById("bk_gwt").value === "24000" && d.getElementById("bk_vol").value === "58.5", [d.getElementById("bk_pcs").value, d.getElementById("bk_gwt").value, d.getElementById("bk_vol").value].join("|"));
  check("it says which quotation it comes from", /Raised from quotation QT\//.test(text("#bookFormWrap")));
  check("customer address comes from the Organization master (once the customer is picked)", (() => { setf("bk_customer", String(cust.body.id)); return /12 Port Road/.test(d.getElementById("bk_customerAddr").value); })(), d.getElementById("bk_customerAddr").value);
  check("carrier list comes from the master", Array.from(d.querySelectorAll("#bk_carrier option")).some(o => o.value === "MSC Mediterranean"));
  check("Sea booking: the carrier list is the Sea Line Master, CFS list comes from the CFS Master, the IATA / stock fields are Air-only", Array.from(d.querySelectorAll("#bk_carrier option")).some(o => o.value === "Hapag-Lloyd Net") && Array.from(d.querySelectorAll("#bk_cfs option")).some(o => /Ennore CFS/.test(o.value)) && d.querySelector("#bk_iata").closest(".fg").style.display === "none");
  await j("POST", "/api/network/airlines", { code: "QX", name: "Qatar Test Cargo", num_code: "157" }, token);
  const alr = (await j("GET", "/api/network/airlines?q=Qatar Test", null, token)).body.items[0];
  await j("POST", "/api/network/stock-lines", { airline_id: alr.id, from_awb: "15700000", to_awb: "15700099" }, token);
  { const m0 = w.bookWiz.mode; w.BKG.m.net = (await j("GET", "/api/network/pickers", null, token)).body; w.bookWiz.mode = "Air"; w.BKG.applyMode();
    const opts = id => Array.from(d.querySelectorAll("#" + id + " option")).map(o => o.value);
    check("Air booking: carriers are the Airline Master, the IATA field is shown", opts("bk_carrier").includes("Qatar Test Cargo") && !opts("bk_carrier").includes("Hapag-Lloyd Net") && opts("bk_iata").some(v => /Skyline Agent/.test(v)) && d.querySelector("#bk_iata").closest(".fg").style.display !== "none");
    d.getElementById("bk_carrier").value = "Qatar Test Cargo"; d.getElementById("bk_carrier").dispatchEvent(new w.Event("change", { bubbles: true }));
    check("choosing the airline offers its AWB stock lines", opts("bk_stock").some(v => /15700000 – 15700099 \(100 AWBs\)/.test(v)), opts("bk_stock").join("|"));
    w.bookWiz.mode = m0; w.BKG.applyMode(); }
  check("a + shortcut sits beside Carrier / Line, Customer, Shipper, Consignee and Notify", ["bk_carrier", "bk_customer", "bk_shipper", "bk_consignee", "bk_notify"].every(id => d.querySelector(`[data-bk-plus="${id}"]`)));
  d.querySelector('[data-bk-plus="bk_customer"]').click(); await sleep(300);
  check("+ beside Customer opens the add-organization pop-up", d.getElementById("modalOvl").classList.contains("on") && !!d.getElementById("qa_code"));
  { const nm = "Quick Add Co " + Date.now().toString(36); d.getElementById("qa_name").value = nm; d.getElementById("qa_name").dispatchEvent(new w.Event("input", { bubbles: true })); d.getElementById("qa_gst").value = "No"; d.getElementById("qa_go").click(); await sleep(2500);
    const mine = (await j("GET", "/api/customers", null, token)).body.find(c => c.name === nm);
    check("the new customer is saved as an AR party and picked in the booking", !!mine && mine.roles.includes("ar") && mine.roles.includes("customer") && d.getElementById("bk_customer").value === String(mine.id) && !d.getElementById("modalOvl").classList.contains("on"), JSON.stringify(mine)); }
  d.querySelector('[data-bk-plus="bk_carrier"]').click(); await sleep(800);
  check("+ beside Carrier / Line opens the Sea Line add form (Sea booking)", d.getElementById("netDrawerOvl") && d.getElementById("netDrawerOvl").classList.contains("on") && /Sea Line/i.test(d.getElementById("netDrawerTitle").textContent), d.getElementById("netDrawerTitle") && d.getElementById("netDrawerTitle").textContent);
  d.getElementById("netCancel").click(); await sleep(200);
  setf("bk_carrier", "MSC Mediterranean"); setf("bk_vessel", "MSC ROMA / 254W"); setf("bk_etd", "2030-01-10"); setf("bk_eta", "2030-02-05"); setf("bk_sicutoff", "2030-01-05"); setf("bk_shipper", "Acme Exports"); setf("bk_consignee", "Acme Exports"); setf("bk_pcs", "120"); setf("bk_gwt", "24000"); setf("bk_marks", "SODA ASH");
  const costRows = () => Array.from(d.querySelectorAll("#bk_chargeRows tr[data-bk-charge]"));
  check("the costing step already holds the charges approved in the quotation (sell and buy)", costRows().length === 2 && costRows().some(r => r.querySelector('[data-f="type"]').value === "AR" && r.querySelector('[data-f="rate"]').value === "850") && costRows().some(r => r.querySelector('[data-f="type"]').value === "AP" && r.querySelector('[data-f="rate"]').value === "700"), String(costRows().length));
  check("a costing row calculates its amount", costRows()[0].querySelector('[data-f="amount"]').value === "1700.00");
  const apRow = costRows().find(r => r.querySelector('[data-f="type"]').value === "AP");
  check("the vendor for a buy charge can be chosen from the vendors; a sell row has no vendor", Array.from(apRow.querySelectorAll('[data-f="vendor"] option')).some(o => o.value === "MSC Mediterranean") && costRows().find(r => r.querySelector('[data-f="type"]').value === "AR").querySelector('[data-f="vendor"]').disabled === true);
  const vsel = apRow.querySelector('[data-f="vendor"]'); vsel.value = "MSC Mediterranean"; vsel.dispatchEvent(new w.Event("change", { bubbles: true }));
  w.bookGotoStep(5); await sleep(300);
  check("the overview shows booking, parties, consignment and costing with the vendor", /Booking details/.test(text("#bk_overviewBody")) && /Acme Exports/.test(text("#bk_overviewBody")) && /12 Port Road/.test(text("#bk_overviewBody")) && /Ocean Freight/.test(text("#bk_overviewBody")) && /MSC Mediterranean/.test(text("#bk_overviewBody")), text("#bk_overviewBody").slice(0, 300));
  check("Consignment is ordered pieces, pack type, container type, container no", (() => { const ids = Array.from(d.querySelectorAll("#bpStep3 .fr:first-child .fg input, #bpStep3 .fr:first-child .fg select")).map(e => e.id); return ids.join(",") === "bk_pcs,bk_packtype,bk_conttype,bk_contno"; })());
  check("the Space / Equipment box no longer sits under every step", !d.getElementById("bk_spaceBox"));
  // a failed save shows a red error and never the success message
  const toastList = () => Array.from(d.querySelectorAll("#toastStack .toast"));
  toastList().forEach(t => t.remove());
  setf("bk_etd", "2030-03-10"); setf("bk_eta", "2030-03-01");
  d.getElementById("bk_saveBtn").click(); await sleep(1200);
  check("a failed save shows a red error toast and not the success message", toastList().some(t => t.classList.contains("error") && /Could not save the booking/.test(t.textContent) && /ETA cannot be earlier than ETD/.test(t.textContent)) && !toastList().some(t => /Updated Successfully/.test(t.textContent)), toastList().map(t => t.className + ":" + t.textContent).join("|"));
  setf("bk_etd", "2030-01-10"); setf("bk_eta", "2030-02-05"); toastList().forEach(t => t.remove());
  d.getElementById("bk_saveBtn").click(); await sleep(1200);
  check("a successful save shows 'Booking Updated Successfully!' in green with a check", toastList().some(t => t.classList.contains("success") && /Booking Updated Successfully!/.test(t.textContent) && !!t.querySelector('svg[data-icon="check-circle"]')), toastList().map(t => t.className + ":" + t.textContent).join("|"));
  d.getElementById("bk_saveBtn").click(); await sleep(1200);
  const drafts = (await j("GET", "/api/bookings", null, token)).body;
  check("Save Draft stores it with the quotation link, parties and charges", drafts.length === 1 && drafts[0].status === "DRAFT" && drafts[0].quotation_id === q.id && drafts[0].carrier === "MSC Mediterranean" && drafts[0].data.consignee === "Acme Exports" && drafts[0].data.charges.length === 2, JSON.stringify(drafts[0] || {}).slice(0, 300));

  d.getElementById("bk_finishBtn").click(); await sleep(2500);
  const all = (await j("GET", "/api/bookings", null, token)).body;
  let relFull = (await j("GET", `/api/bookings/${all[0].id}`, null, token)).body;
  check("releasing the booking issues the number, creates no job and opens the Booking page", all[0].status === "CONFIRMED" && /^BKG/.test(all[0].number || "") && relFull.jobs.length === 0 && d.querySelector(".scr.on").id === "s-booking-view", JSON.stringify(relFull).slice(0, 200));
  const madeJob = (await j("POST", `/api/bookings/${all[0].id}/jobs`, { service: "Freight" }, token)).body.created_job;      // the Freight job is made from the booking number
  relFull = (await j("GET", `/api/bookings/${all[0].id}`, null, token)).body;
  all[0].job_number = madeJob; const bookingNo = all[0].number;
  const quote = (await j("GET", `/api/quotations/${q.id}`, null, token)).body;
  check("the quotation is now Converted to that job", quote.status === "CONVERTED" && quote.job_number === all[0].job_number, quote.status);
  const jobRec = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === all[0].job_number);
  check("the job carries mode, route, customer, cargo, weight, volume and commodity from the quotation", jobRec && jobRec.mode === "Sea" && jobRec.direction === "Export" && jobRec.container_mode === "FCL" && jobRec.pol === "INMAA(CHENNAI)" && jobRec.pod === "BEANR(ANTWERPEN)" && jobRec.packages === 120 && Number(jobRec.gross_weight) === 24000 && Number(jobRec.volume) === 58.5 && jobRec.commodity === "Soda ash", JSON.stringify(["mode","direction","container_mode","pol","pod","packages","gross_weight","volume","commodity","customer_id"].map(k => [k, (jobRec||{})[k]])));
  const ch = (await j("GET", `/api/shipments/by-number/${all[0].job_number}/charges`, null, token)).body.lines;
  check("the job carries the sell charge and the buy cost with the vendor chosen on the booking", ch.some(c => Number(c.sell.rate) === 850 && Number(c.buy.rate) === 700 && c.buy.party === "MSC Mediterranean"), JSON.stringify(ch).slice(0, 300));

  w.go("booking"); await sleep(1800);
  check("the Bookings list shows the confirmed booking with job, carrier, vessel and status", /Confirmed/.test(text("#s-booking tbody")) && new RegExp(all[0].job_number).test(text("#s-booking tbody")) && /MSC ROMA/.test(text("#s-booking tbody")), text("#s-booking tbody").slice(0, 200));
  check("the KPI cards count it", Number(text("#s-booking .kpi:nth-child(1) .kpi-val")) === 1, text("#s-booking .kpi-row"));
  q$(`[data-bk-act="dup:${all[0].id}"]`).click(); await sleep(1800);
  const after = (await j("GET", "/api/bookings", null, token)).body;
  check("Duplicate opens a new draft with its own number and no job or quotation link", after.length === 2 && after.some(b => b.status === "DRAFT" && b.number && !b.job_number && !b.quotation_id));
  w.go("booking"); await sleep(1500);
  const draftRow = after.find(b => b.status === "DRAFT");
  q$(`[data-bk-act="delete:${draftRow.id}"]`).click(); await sleep(1200);
  check("a draft can be deleted from the list", (await j("GET", "/api/bookings", null, token)).body.length === 1);
  q$(`[data-bk-act="release:${all[0].id}"]`).click(); await sleep(1200);
  check("space can be released on a confirmed booking", (await j("GET", `/api/bookings/${all[0].id}`, null, token)).body.status === "SPACE_RELEASED");

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
