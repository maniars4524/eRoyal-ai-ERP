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

  const hasHead = () => !!q("#qkConts");
  check("the quotation header has a Containers in this shipment block for a sea shipment", hasHead());
  // an enquiry with two container types and the places of receipt / loading / discharge / delivery
  w.QK.prefillFromEnquiry({ id: "ENQ-1", company: "Acme Exports", pol: "INMAA", pod: "BEANR", origin: "Chennai ICD", destination: "Antwerp CY", finalDelivery: "Brussels depot", commodity: "Garments", containerType: "20GP", containerQty: "2", containers: [{ type: "20GP", qty: "2" }, { type: "40HC", qty: "1" }] });
  w.QK.renderAll && w.QK.renderAll(true); await sleep(500);
  const H = () => w.QK.head;
  check("enquiry containers come into the quotation header", JSON.stringify(H().containers) === JSON.stringify([{ type: "20GP", qty: "2" }, { type: "40HC", qty: "1" }]), JSON.stringify(H().containers));
  check("place of receipt and final destination come from the enquiry", /Chennai ICD/.test(H().origin || "") && /Brussels depot/.test(H().final_destination || ""), JSON.stringify([H().origin, H().final_destination, H().pol, H().pod]));
  check("loading and discharge ports come from the enquiry", /INMAA/.test(H().pol || "") && /BEANR/.test(H().pod || ""), JSON.stringify([H().pol, H().pod]));
  check("the container block shows both rows", d.querySelectorAll("#qkConts [data-qc$=':type']").length === 2, q("#qkConts") && q("#qkConts").innerHTML.slice(0, 200));
  // container-wise rates: types and quantities are already there, only rates are typed
  await act("containers", '[data-k="sell:new"]');
  const pre = Array.from(d.querySelectorAll("#qkCont tbody tr")).map(tr => tr.textContent.replace(/\s+/g, " ").trim());
  check("the container-wise rates pop-up already lists the shipment's containers with their quantities", pre.length === 2 && /20GP/.test(pre[0]) && /40HC/.test(pre[1]) && q('[data-cq="0:qty"]').value === "2" && q('[data-cq="1:qty"]').value === "1", JSON.stringify(pre));
  setf(q("#qkcCharge"), "Documentation", "change"); setf(q('[data-cq="0:rate"]'), "100"); setf(q('[data-cq="1:rate"]'), "180");
  q("#qkcSave").click(); await sleep(300);
  const line = w.QK.sell[w.QK.sell.length - 1];
  check("only the rates were typed and the line is saved with both container types", line && line.container_breakup.length === 2 && line.container_breakup[0].qty === "2" && line.container_breakup[1].rate === "180", JSON.stringify(line && line.container_breakup));
  // a manual edit of the shipment containers
  q("[data-qc-add]").click(); await sleep(100);
  check("another container type can be added in the header", d.querySelectorAll("#qkConts [data-qc$=':type']").length === 3 && H().containers.length === 3);
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
