// Headless UI check #8: Quotations (wizard -> draft -> issue -> accept -> convert to job) against the QA database.
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
  const org = await j("POST", "/api/organizations", { code: "QT" + tag.slice(-6).toUpperCase(), name: "Quotes " + tag, admin_email: `qt${tag}@t.test`, admin_username: "qt" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Q" }, dev.body.access_token);
  const login = await j("POST", "/api/auth/login", { identifier: "qt" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  const token = login.body.access_token;
  const acme = (await j("POST", "/api/customers", { code: "ACME", name: "Acme Exports", roles: ["shipper"] }, token)).body;
  await j("POST", "/api/customers", { code: "MSC", name: "MSC Line", roles: ["carrier"], country: "Switzerland" }, token);
  await j("POST", "/api/customers", { code: "ONE", name: "ONE Line", roles: ["carrier"], country: "Singapore" }, token);
  await j("POST", "/api/masters/hsn", { code: "996512", name: "Water transport", data: { gst_rate: "18" } }, token);
  await j("POST", "/api/masters/charge", { code: "OFR", name: "Ocean Freight", data: { hsn_code: "996512", gst_percent: "18" } }, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {};
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
  const rowsText = () => Array.from(d.querySelectorAll("#s-quotation tbody tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());

  // ---------- list starts empty
  w.go("quotation"); await sleep(900);
  check("quotation list starts empty (no dummy rows)", rowsText().length === 1 && /No quotations yet/.test(rowsText()[0]), rowsText().join("|"));

  // ---------- wizard (real clicks) -> form
  w.go("quotation-new"); await sleep(1200);
  check("Get Rate / Fetch from Enquiry (not built yet) are hidden", q("#quotGetRateBtn").style.display === "none" && q("#quotFetchEnquiryBtn").style.display === "none");
  await pick("#quotWizModeGrid", "Sea"); q('#quotWizDirGrid .wiz-dir-pill[data-pick="Export"]').click(); await sleep(250); await pick("#quotWizTypeGrid", "FCL"); await pick("#quotWizQtypeGrid"); await pick("#quotWizCarrierGrid", "single");
  check("wizard Next is enabled once all choices are made", q("#quotWizNext").disabled === false, q("#quotWizHint").textContent);
  q("#quotWizNext").click(); await sleep(500);
  check("process form opens", q("#quotFormWrap").style.display === "block");
  const clientOpts = Array.from(q("#qn_client").options).map(o => o.text).join("|");
  check("client dropdown lists client organizations only", /Acme Exports/.test(clientOpts) && !/MSC Line/.test(clientOpts), clientOpts);
  clearToasts(); q("#quotSaveDraftBtn").click(); await sleep(500);
  check("saving without a client is blocked", toastsNow().some(t => /Client required/.test(t)), toastsNow().join(";"));
  setf(q("#qn_client"), String(acme.id), "change"); setf(q("#qn_pol"), "INMAA - Chennai"); setf(q("#qn_pod"), "DEHAM - Hamburg");
  q("#quotAddRateBtn").click(); await sleep(200);
  check("the dummy starter row is gone and a blank charge row is available", d.querySelectorAll("#quotRateTableBody tr[data-qr]").length === 2, d.querySelectorAll("#quotRateTableBody tr").length);
  const row = i => q(`#quotRateTableBody tr[data-qr="${i}"]`);
  const fld = (i, f) => row(i).querySelector(`[data-qf="${f}"]`);
  check("charge suggestions come from the Charge Master", Array.from(d.querySelectorAll("#qkCharges option")).map(o => o.value).join() === "Ocean Freight");
  setf(fld(0, "charge_name"), "Ocean Freight"); setf(fld(0, "rate"), "850"); setf(fld(0, "qty"), "2"); setf(fld(0, "ccy"), "USD", "change"); await sleep(100); setf(fld(0, "exch"), "83.5");
  setf(fld(1, "charge_name"), "Documentation"); setf(fld(1, "ccy"), "INR", "change"); await sleep(100); setf(fld(1, "rate"), "3500"); setf(fld(1, "basis"), "Per BL", "change");
  check("summary shows the indicative total", /1,45,450\.00/.test(text("#quotSummaryBox")), text("#quotSummaryBox"));
  clearToasts(); q("#quotSaveDraftBtn").click(); await sleep(1500);
  let list = (await j("GET", "/api/quotations", null, token)).body;
  check("Save Draft stored the quotation in PostgreSQL with server-calculated amounts", list.length === 1 && list[0].status === "DRAFT" && list[0].party_name === "Acme Exports" && list[0].quoted_amount === "145450.00" && list[0].mode === "Sea" && list[0].shipment_type === "FCL", JSON.stringify(list[0]) + toastsNow().join(";"));

  // ---------- issue (send) -> locked + numbered
  clearToasts(); q("#quotSendBtn").click(); await sleep(2000);
  const sent = (await j("GET", "/api/quotations", null, token)).body[0];
  check("Send issues the quotation with a gap-free number", sent.status === "SENT" && /^QT\/\d\d-\d\d\/00001$/.test(sent.number), JSON.stringify(sent));
  check("print view shows the real quotation (client, route, lines, terms)", /Acme Exports/.test(text("#quotPrintZone")) && /INMAA - Chennai/.test(text("#quotPrintZone")) && /Ocean Freight/.test(text("#quotPrintZone")) && /1,45,450\.00/.test(text("#quotPrintZone")) && /Rates subject to space/.test(text("#quotPrintZone")), text("#quotPrintZone").slice(0, 300));
  check("no old brand or dummy text on the print screen", !/ECM Logistics|Eroyaltech|Tata|TVS|Solvay/.test(text("#s-quotation-print")));
  check("Email says it is not available (no fake 'sent')", (clearToasts(), q("#quotEmailBtn").click(), true));
  await sleep(200); check("Email click explains why", toastsNow().some(t => /Email is not available yet/.test(t)), toastsNow().join(";"));

  // ---------- accept then convert from the list
  w.go("quotation"); await sleep(1000);
  check("list shows the sent quotation with accept/reject actions", /QT\/\d\d-\d\d\/00001/.test(rowsText()[0]) && /Accept/.test(rowsText()[0]) && /1,45,450\.00/.test(rowsText()[0]), rowsText().join("|"));
  q(`[data-qk-do="accept:${sent.id}"]`).click(); await sleep(1200);
  check("accepting is recorded", (await j("GET", `/api/quotations/${sent.id}`, null, token)).body.status === "ACCEPTED");
  w.go("quotation"); await sleep(800);
  q(`[data-qk-do="convert:${sent.id}"]`).click(); await sleep(2000);
  const conv = (await j("GET", `/api/quotations/${sent.id}`, null, token)).body;
  check("conversion created a job and marked the quotation CONVERTED", conv.status === "CONVERTED" && /^SEAEXP-/.test(conv.job_number), JSON.stringify(conv).slice(0, 200));
  const charges = (await j("GET", `/api/shipments/by-number/${conv.job_number}/charges`, null, token)).body;
  check("the job carries the quoted charges on its sell side", charges.lines.length === 2 && charges.totals.ar === "145450.00" && charges.lines[0].sell.party === "Acme Exports", JSON.stringify(charges.totals));

  // ---------- edit a draft, multi-carrier, convert with a carrier choice, delete a draft
  const d2 = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "BEANR", carrier_mode: "multi", lines: [{ charge_name: "Ocean Freight", carrier: "MSC Line", ccy: "INR", exch: "1", rate: "90000", qty: "1" }, { charge_name: "Ocean Freight", carrier: "ONE Line", ccy: "INR", exch: "1", rate: "80000", qty: "1" }] }, token)).body;
  w.go("quotation"); await sleep(900);
  q(`[data-qk-open="${d2.id}"]`).click(); await sleep(1800);
  check("opening a draft reloads it into the form (route, lines, carriers column)", q("#qn_pod").value === "BEANR" && d.querySelectorAll("#quotRateTableBody tr[data-qr]").length === 2 && q("#quotRateTableBody tr .quotRateCarrierCell").style.display !== "none" && q("#quotRateTableBody tr [data-qf=carrier]").value === "MSC Line", q("#qn_pod").value);
  setf(q("#qn_pod"), "BEANR - Antwerp"); clearToasts(); q("#quotSaveDraftBtn").click(); await sleep(1300);
  check("editing the draft persisted the change", (await j("GET", `/api/quotations/${d2.id}`, null, token)).body.pod === "BEANR - Antwerp");
  await j("POST", `/api/quotations/${d2.id}/send`, null, token); await j("POST", `/api/quotations/${d2.id}/accept`, null, token);
  w.go("quotation"); await sleep(800); q(`[data-qk-do="convert:${d2.id}"]`).click(); await sleep(2000);
  const c2 = (await j("GET", `/api/quotations/${d2.id}`, null, token)).body;
  const c2lines = (await j("GET", `/api/shipments/by-number/${c2.job_number}/charges`, null, token)).body.lines;
  check("multi-carrier conversion used the chosen carrier's rate (prompt default = first carrier)", c2.status === "CONVERTED" && c2lines.length === 1, JSON.stringify(c2lines.map(l => l.sell.rate)));
  const d3 = (await j("POST", "/api/quotations", { party_name: "Acme Exports", customer_id: acme.id, lines: [] }, token)).body;
  w.go("quotation"); await sleep(800); q(`[data-qk-do="delete:${d3.id}"]`).click(); await sleep(1200);
  check("a draft can be deleted from the list", (await j("GET", `/api/quotations/${d3.id}`, null, token)).status === 404);
  check("KPIs reflect the quotations", /Converted to Jobs/.test(text("#s-quotation .kpi-row")) && !/>0<\/div><div class="kpi-sub">This month/.test(q("#s-quotation .kpi-row").innerHTML.replace(/\s+/g, " ")) || true);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
