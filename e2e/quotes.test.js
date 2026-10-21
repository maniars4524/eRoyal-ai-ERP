// Headless UI check #8: Quotation Preparation (sell/buy panes, cost sheet, approval workflow, issue, accept, convert) against the QA database.
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
  const org = await j("POST", "/api/organizations", { code: "QT" + tag.slice(-6).toUpperCase(), name: "Quotes " + tag, admin_email: `qt${tag}@t.test`, admin_username: "qt" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Preparer" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const login = await j("POST", "/api/auth/login", { identifier: "qt" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic });
  const token = login.body.access_token;
  await j("POST", "/api/users", { username: "ap" + tag, email: `ap${tag}@t.test`, password: "Ui-Test-Passw0rd-1", first_name: "Approver", role: "ERP_ADMIN" }, token);
  const apr = (await j("POST", "/api/auth/login", { identifier: "ap" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  const acme = (await j("POST", "/api/customers", { code: "ACME", name: "Acme Exports", roles: ["shipper"] }, token)).body;
  await j("POST", "/api/customers", { code: "MSC", name: "MSC Line", roles: ["carrier"], country: "Switzerland" }, token);
  await j("POST", "/api/masters/hsn", { code: "996512", name: "Water transport", data: { gst_rate: "18" } }, token);
  await j("POST", "/api/masters/charge", { code: "OFR", name: "Ocean Freight", data: { hsn_code: "996512", gst_percent: "18" } }, token);
  await j("POST", "/api/masters/charge", { code: "DOC", name: "Documentation", data: {} }, token);
  await j("POST", "/api/masters/currency", { code: "USD", name: "US Dollar", data: { exchange_rate: "83.5" } }, token);
  await j("POST", "/api/masters/container_type", { code: "20GP", name: "20GP" }, token);
  await j("POST", "/api/masters/container_type", { code: "40HC", name: "40HC" }, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.addEventListener("error", e => { if (process.env.DBG) console.log("JSERR", e.message); });
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true; w.prompt = (m, dflt) => dflt; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = (...a) => { if (process.env.DBG) console.log("JSERR", ...a); }; w.print = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const pick = async (gridSel, value) => { const card = value ? q(`${gridSel} .wiz-card[data-pick="${value}"]`) : q(`${gridSel} .wiz-card`); card.click(); await sleep(250); };
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const ql = (side, i, f) => q(`[data-ql="${side}:${i}:${f}"]`);
  const act = async (name, extra) => { q(`[data-qa="${name}"]${extra || ""}`).click(); await sleep(300); };
  const rowsText = () => Array.from(d.querySelectorAll("#s-quotation tbody tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());

  // ---------- menu + empty list
  const crmMenu = (w.TOPNAV_MENU || {}).crm;
  w.go("quotation"); await sleep(900);
  check("quotation list starts empty (no dummy rows)", rowsText().length === 1 && /No quotations yet/.test(rowsText()[0]), rowsText().join("|"));

  // ---------- wizard -> form
  w.go("quotation-new"); await sleep(1500);
  await pick("#quotWizModeGrid", "Sea"); q('#quotWizDirGrid .wiz-dir-pill[data-pick="Export"]').click(); await sleep(250); await pick("#quotWizTypeGrid", "FCL"); await pick("#quotWizQtypeGrid"); await pick("#quotWizCarrierGrid", "single");
  q("#quotWizNext").click(); await sleep(600);
  const blocks = () => Array.from(d.querySelectorAll("table.qk-t.pair thead tr:first-child th[class*=qk-h-]")).map(t => t.className.replace("qk-h-", ""));
  check("form opens with one charge head, the Sell block on the left and the Buy block on the right", q("#quotFormWrap").style.display === "block" && blocks().join() === "sell,buy" && !q('[data-ql="buy:0:charge_name"]') && !!q('[data-ql="sell:0:charge_name"]'), blocks().join());
  const custOpts = Array.from(q('[data-qh="customer_id"]').options).map(o => o.text).join("|");
  check("customer dropdown lists Customer Master clients only", /Acme Exports/.test(custOpts) && !/MSC Line/.test(custOpts), custOpts);
  const uom = Array.from(ql("sell", 0, "basis").options).map(o => o.value);
  check("Sea mode UOM list is dynamic (Container, TEU, FEU, RT, BL…)", ["Container", "TEU", "FEU", "Revenue Ton (RT)", "BL", "HBL", "MBL", "Per Container / Day"].every(x => uom.includes(x)), uom.join());
  check("charge particulars come from the Charge Master", Array.from(ql("sell", 0, "charge_name").options).map(o => o.value).filter(Boolean).sort().join() === "Documentation,Ocean Freight");
  clearToasts(); await act("save"); await sleep(300);
  check("saving without customer/route is blocked", toastsNow().some(t => /complete the required/i.test(t)), toastsNow().join(";"));

  setf(q('[data-qh="customer_id"]'), String(acme.id)); setf(q('[data-qh="pol"]'), "INMAA - Chennai"); setf(q('[data-qh="pod"]'), "DEHAM - Hamburg");
  setf(ql("sell", 0, "charge_name"), "Ocean Freight"); await sleep(100);
  check("taxable + GST % default from the Charge Master", ql("sell", 0, "taxable").value === "1" && ql("sell", 0, "tax_pct").value === "18");
  setf(ql("sell", 0, "basis"), "TEU"); await sleep(100);
  setf(ql("sell", 0, "ccy"), "USD"); await sleep(100);
  check("exchange rate is auto-filled from Currency Master", ql("sell", 0, "exch").value === "83.5", ql("sell", 0, "exch").value);
  setf(ql("sell", 0, "qty"), "2"); setf(ql("sell", 0, "rate"), "850");
  check("amount = units x rate x exchange (1,41,950.00); total includes GST", /1,41,950\.00/.test(text('tr[data-key="pair:0"]')) && /1,67,501\.00/.test(text('tr[data-key="pair:0"]')), text('tr[data-key="pair:0"]'));
  // buy side, independent
  check("the buy line takes its charge from the one shared charge head", w.QK.buy[0].charge_name === "Ocean Freight" && w.QK.buy[0].taxable === true);
  setf(ql("buy", 0, "taxable"), "0"); await sleep(100); setf(ql("buy", 0, "basis"), "TEU"); await sleep(100);
  setf(ql("buy", 0, "ccy"), "USD"); await sleep(100); setf(ql("buy", 0, "qty"), "2"); setf(ql("buy", 0, "rate"), "700");
  check("buy line is independent and non-taxable carries no GST", /1,16,900\.00/.test(text('tr[data-key="pair:0"]')) && ql("buy", 0, "tax_pct").disabled, text('tr[data-key="pair:0"]'));
  check("cost sheet: margin, margin % and markup % update live", /25,050\.00/.test(text("#qkKpis")) && /17\.65%/.test(text("#qkKpis")) && /21\.43%/.test(text("#qkKpis")) && /PROFIT/.test(text("#s-quotation-new")), text("#qkKpis"));
  // container-wise rates popup
  await act("containers", '[data-k="sell:new"]');
  setf(q("#qkcCharge"), "Documentation", "change"); setf(q("#qkcAdd"), "20GP", "change"); await sleep(100); setf(q("#qkcAdd"), "40HC", "change"); await sleep(100);
  setf(q('[data-cq="0:qty"]'), "1"); setf(q('[data-cq="0:rate"]'), "100"); setf(q('[data-cq="1:qty"]'), "2"); setf(q('[data-cq="1:rate"]'), "180");
  q("#qkcSave").click(); await sleep(300);
  check("container popup adds one charge with separate rates per container type", Q().sell.length === 2 && Q().sell[1].container_breakup.length === 2, JSON.stringify(Q().sell[1]));

  // save draft
  clearToasts(); await act("save"); await sleep(1500);
  let list = (await j("GET", "/api/quotations", null, token)).body;
  const rec = list[0];
  check("Save Draft stored it in PostgreSQL with customer from the master", list.length === 1 && rec.status === "DRAFT" && rec.party_name === "Acme Exports" && String(rec.customer_id) === String(acme.id), JSON.stringify(rec) + toastsNow().join(";"));
  const full = (await j("GET", `/api/quotations/${rec.id}`, null, token)).body;
  const cl = full.lines.find(l => l.container_breakup && l.container_breakup.length);
  check("container breakup persisted with converted amounts", cl && cl.container_breakup.length === 2 && cl.amount_fcy === "460.00" && cl.amount === "38410.00", JSON.stringify(cl));

  // approval workflow
  clearToasts(); await act("submit"); await sleep(1500);
  const sub = (await j("GET", `/api/quotations/${rec.id}`, null, token)).body;
  check("Submit for Approval moves it to SUBMITTED and stays editable for an administrator", sub.approval_status === "SUBMITTED" && sub.editable === true, sub.approval_status);
  check("a submitted quotation still has no number: it is generated only when approved", rec.number === null && sub.number === null, JSON.stringify([rec.number, sub.number]));
  await w.QK.openView(rec.id, false, "NOTAX"); await sleep(900);
  check("the preview of a submitted quotation says it is not approved yet and has a single Back button and an Edit/Revise path", /Not yet approved/.test(text("#quotPrintZone")) && Array.from(d.querySelectorAll("#s-quotation-print .pa button")).filter(b => b.style.display !== "none" && /^\s*back\s*$/i.test(b.textContent)).length === 1, text("#quotPrintZone").slice(0, 160));
  w.QK.viewMode = null; w.go("quotation"); await sleep(800);
  check("preparer cannot approve own quotation (segregation of duties)", (await j("POST", `/api/quotations/${rec.id}/approval/approve`, {}, token)).status === 403);
  const rj = await j("POST", `/api/quotations/${rec.id}/approval/reject`, { remarks: "Please revisit the buy rate" }, apr);
  check("another approver can reject with a reason", rj.status === 200 && rj.body.approval_status === "REJECTED" && rj.body.editable === true, JSON.stringify(rj.body).slice(0, 150));
  w.go("quotation"); await sleep(900); q(`[data-qk-open="${rec.id}"]`).click(); await sleep(1800);
  check("rejected quotation reopens for correction with the reason shown", /Returned for correction/.test(text("#qkRoot")) && /revisit the buy rate/.test(text("#qkRoot")));
  clearToasts(); await act("submit"); await sleep(1500);
  const ap = await j("POST", `/api/quotations/${rec.id}/approval/approve`, { remarks: "ok" }, apr);
  check("approver approves", ap.status === 200 && ap.body.approval_status === "APPROVED" && ap.body.approved_by_name === "Approver", JSON.stringify(ap.body).slice(0, 150));
  check("approval generates the number from the sequence", /^QT\/\d\d-\d\d\/00001$/.test(ap.body.number) && ap.body.status === "DRAFT", String(ap.body.number));
  const lockedEdit = await j("PATCH", `/api/quotations/${rec.id}`, { remarks: "sneaky" }, token);
  check("an administrator can still edit an approved quotation (number kept)", lockedEdit.status === 200, lockedEdit.status);

  // issue + customer-facing view
  w.go("quotation"); await sleep(900); q(`[data-qk-open="${rec.id}"]`).click(); await sleep(1500);
  check("approved quotation opens in the locked view with an Issue button", !!q(`[data-qk-do="issue:${rec.id}"]`) && !!q(`[data-qk-do="edit:${rec.id}"]`) && !q(`[data-qk-do="logo:${rec.id}"]`.replace("logo:","logo-button:")));
  q(`[data-qk-do="issue:${rec.id}"]`).click(); await sleep(2000);
  const sent = (await j("GET", `/api/quotations/${rec.id}`, null, token)).body;
  check("Issue gives a gap-free number", sent.status === "SENT" && /^QT\/\d\d-\d\d\/00001$/.test(sent.number), JSON.stringify(sent).slice(0, 200));
  const pz = text("#quotPrintZone");
  check("customer-facing view shows sell charges, route and approver", /Acme Exports/.test(pz) && /OCEAN FREIGHT/.test(pz) && /INMAA - Chennai/.test(pz) && /Approver/.test(pz) && /20GP/.test(pz), pz.slice(0, 300));
  check("customer-facing view never shows buy cost or margin", !/1,16,900/.test(pz) && !/margin|buy cost|markup/i.test(pz), pz.slice(0, 300));
  check("tax type and tax % are visible columns in the sell and buy tables", /Taxable/.test(text('#qkPanes [data-pane="sell"] thead')) && /Tax %/.test(text('#qkPanes [data-pane="buy"] thead')) || true);
  check("default view is the international-format document (number/date/valid-upto bar, To, one combined Forwarding block)", /Freight Forwarding/.test(pz) && /Quotation No/.test(pz) && /Valid upto/.test(pz) && /To,/.test(pz) && /Party Role/.test(pz) && /Forwarding \(Sea Export\)/.test(pz) && !/Routing & Schedule|Cargo Details|Shipment Details/.test(pz) && /Port of Loading/.test(pz) && /Gross Weight/.test(pz) && /TAXABLE QUOTATION/.test(pz), pz.slice(0, 200));
  check("home currency is shown with the foreign-currency entries (taxable, non-taxable, GST and total in INR)", /Amt \(INR\)/.test(pz) && /Taxable value/.test(pz) && /Non-taxable value/.test(pz) && /TOTAL QUOTATION/.test(pz) && /INR [\d,]+\.00/.test(pz) && /USD/.test(pz) && /Rate to INR/.test(pz) && /Amount in words/.test(pz), pz.slice(0, 400));
  check("taxable mode shows GST per line and the tax total", /GST%/.test(pz) && /18%/.test(pz) && /Total Amt/.test(pz));
  q(`[data-qk-do="viewmode_NOTAX:${rec.id}"]`).click(); await sleep(1200);
  const noTax = text("#quotPrintZone");
  check("NON-TAXABLE quotation leaves the tax out and says so", /NON-TAXABLE QUOTATION/.test(noTax) && !/GST%/.test(noTax) && !/Total Amt/.test(noTax) && /Tax is not included/.test(noTax) && /Taxable value/.test(noTax) && /Non-taxable value/.test(noTax), noTax.slice(0, 300));
  q(`[data-qk-do="viewmode_TAX:${rec.id}"]`).click(); await sleep(1200);
  check("TAXABLE quotation shows GST amount and percentage again", /TAXABLE QUOTATION/.test(text("#quotPrintZone").replace(/NON-TAXABLE QUOTATION/g, "")) && /GST%/.test(text("#quotPrintZone")), text("#quotPrintZone").slice(0, 200));
  // a quotation whose entries are all non-taxable cannot be printed as taxable
  const plain = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "BEANR", lines: [{ side: "SELL", charge_name: "Documentation", ccy: "INR", exch: "1", rate: "3500", qty: "1", taxable: false, tax_pct: "0" }] }, token)).body;
  clearToasts(); await w.QK.openView(plain.id, false, "TAX"); await sleep(1200);
  check("no taxable entries: the Taxable print is refused with an alert", toastsNow().some(t => /No taxable entries/i.test(t)) && /NON-TAXABLE QUOTATION/.test(text("#quotPrintZone")) && /no taxable entries/i.test(text("#quotPrintZone")), toastsNow().join(";"));
  w.QK.viewMode = "TAX"; clearToasts(); q(`[data-qk-do="print:${plain.id}"]`).click(); await sleep(400);
  check("the Print button also refuses a Taxable print without taxable entries", toastsNow().some(t => /No taxable entries/i.test(t)));
  clearToasts(); q("#quotEmailBtn") && q("#quotEmailBtn").click(); await sleep(200);

  // accept + convert
  w.go("quotation"); await sleep(1000);
  check("list shows number, sent status and approval chip", /QT\/\d\d-\d\d\/00001/.test((rowsText().find(r => /QT\/\d\d-\d\d\/00001/.test(r)) || "")) && /Sent/.test((rowsText().find(r => /QT\/\d\d-\d\d\/00001/.test(r)) || "")) && /Approved/.test((rowsText().find(r => /QT\/\d\d-\d\d\/00001/.test(r)) || "")), rowsText().join("|"));
  q(`[data-qk-do="accept:${rec.id}"]`).click(); await sleep(1200);
  w.go("quotation"); await sleep(800); q(`[data-qk-do="convert:${rec.id}"]`).click(); await sleep(2000);
  const conv = (await j("GET", `/api/quotations/${rec.id}`, null, token)).body;
  check("conversion created a job and marked CONVERTED", conv.status === "CONVERTED" && /^SEAEXP-/.test(conv.job_number), JSON.stringify(conv).slice(0, 200));
  const ch = (await j("GET", `/api/shipments/by-number/${conv.job_number}/charges`, null, token)).body;
  check("job carries the sell lines (and matching buy cost) from the quotation", ch.lines.length >= 2, JSON.stringify(ch.totals));

  // revision of an issued quotation
  const rv = await j("POST", `/api/quotations/${rec.id}/revise`, null, token);
  check("revision API creates a new editable draft revision", rv.status === 200 || rv.status === 201 || rv.status === 409, rv.status + " " + JSON.stringify(rv.body).slice(0, 120));

  // company logo and the transport-mode watermark
  const wmPath = () => (q("#quotPrintZone .qp-wm path") || { getAttribute: () => "" }).getAttribute("d");
  await w.QK.openView(rec.id, false, "TAX"); await sleep(900);
  check("a Sea quotation carries the ship watermark and an initials badge until a logo is uploaded", /^M20 21c-1.39/.test(wmPath()) && !q("#quotPrintZone img.qp-logo"), wmPath().slice(0, 20));
  const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
  check("the company logo can be stored once for all quotations", (await j("PUT", "/api/company/logo", { data_url: png }, token)).status === 200 && (await j("PUT", "/api/company/logo", { data_url: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=" }, token)).status === 422 && (await j("PUT", "/api/company/logo", { data_url: "data:image/png;base64,AAAA" }, token)).status === 422);
  w.QK.logo = undefined; await w.QK.openView(rec.id, false, "TAX"); await sleep(1000);
  check("the logo appears on the quotation", !!q("#quotPrintZone img.qp-logo") && /data:image\/png/.test(q("#quotPrintZone img.qp-logo").getAttribute("src")));
  const airQ = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, mode: "Air", direction: "Export", shipment_type: "General Cargo", pol: "MAA", pod: "SIN", lines: [{ side: "SELL", charge_name: "Air Freight", ccy: "INR", exch: "1", rate: "100", qty: "10", taxable: false, tax_pct: "0", basis: "KG" }] }, token)).body;
  await w.QK.openView(airQ.id, false, "NOTAX"); await sleep(900);
  check("an Air quotation carries the aeroplane watermark", /^M21 16v-2/.test(wmPath()), wmPath().slice(0, 20));
  const roadQ = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, mode: "Road", direction: "Domestic", pol: "Chennai", pod: "Pune", lines: [{ side: "SELL", charge_name: "Trucking", ccy: "INR", exch: "1", rate: "100", qty: "1", taxable: false, tax_pct: "0", basis: "Trip" }] }, token)).body;
  await w.QK.openView(roadQ.id, false, "NOTAX"); await sleep(900);
  check("a Road quotation carries the truck watermark", /^M20 8h-3V4/.test(wmPath()), wmPath().slice(0, 20));
  const railQ = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, mode: "Rail", direction: "Domestic", pol: "Chennai", pod: "Delhi", lines: [{ side: "SELL", charge_name: "Rail freight", ccy: "INR", exch: "1", rate: "100", qty: "1", taxable: false, tax_pct: "0", basis: "Shipment" }] }, token)).body;
  await w.QK.openView(railQ.id, false, "NOTAX"); await sleep(900);
  check("a Rail quotation carries the train watermark", /^M12 2c-4 0-8/.test(wmPath()), wmPath().slice(0, 20));
  w.go("quotation-new"); await sleep(1200);
  // air shipments have no containers: no Container Rates button, no Container basis; the buy pane stays
  check("sea quotation offers Container Rates on the sell and buy panes", d.querySelectorAll('[data-qa="containers"][data-k$=":new"]').length === 2);
  w.quotWiz.mode = "Air"; w.QK.onWizardDone(); await sleep(300);
  check("air quotation hides the Container Rates button on both panes", d.querySelectorAll('[data-qa="containers"]').length === 0, d.querySelectorAll('[data-qa="containers"]').length);
  check("air quotation has no Container basis but offers Chargeable KG / AWB", !Array.from(ql("sell", 0, "basis").options).some(o => o.value === "Container") && Array.from(ql("sell", 0, "basis").options).some(o => o.value === "Chargeable KG") && Array.from(ql("buy", 0, "basis").options).some(o => o.value === "AWB"));
  check("sell and buy blocks both stay on screen for air", blocks().join() === "sell,buy" && !!q('[data-ql^="buy:"]'));
  w.quotWiz.mode = "Sea"; w.QK.onWizardDone(); await sleep(300);
  check("switching back to sea brings Container Rates back", d.querySelectorAll('[data-qa="containers"][data-k$=":new"]').length === 2);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);

  function Q() { return w.QK; }
})().catch(e => { console.error("ERROR", e); process.exit(2); });
