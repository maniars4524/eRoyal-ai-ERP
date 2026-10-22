// Headless UI check: an enquiry that becomes a quotation does not ask for mode / direction / type again; it asks which extra services the customer needs (customs clearance, transport, warehouse), pre-ticked from the enquiry.
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
  const org = await j("POST", "/api/organizations", { code: "EQ" + tag.slice(-5).toUpperCase(), name: "Enqquote " + tag, admin_email: `eq${tag}@t.test`, admin_username: "eq" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "E" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "eq" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), shown = s => q(s) && q(s).style.display !== "none";
  await sleep(3500);
  const enq = { id: "ENQ-T1", company: "Acme Exports", shipmentType: "Sea Export FCL", modeOfTransport: "Sea", customsClearance: "Yes", pickup: "No", delivery: "Yes", origin: "Chennai", destination: "Singapore", commodity: "Garments", packages: 10, grossWeight: "500" };
  w.go("quotation-new"); await sleep(2500);
  check("a quotation started directly still opens the full wizard", shown("#quotWizModeGrid") && /Select Quotation Details/.test(q("#quotWizTitle").textContent));
  w.applyEnquiryToQuotWiz(enq); await sleep(600);
  check("from an enquiry the mode, direction and type steps are not asked again", !shown("#quotWizModeGrid") && !shown("#quotWizDirWrap") && !shown("#quotWizTypeWrap") && !shown("#quotWizQtypeWrap") && !shown("#quotWizModeLbl"));
  check("it asks about additional services instead", shown("#quotEnqServices") && /Additional services/.test(q("#quotWizTitle").textContent) && /Sea/.test(q("#quotEnqServices").textContent) && /Export/.test(q("#quotEnqServices").textContent) && /FCL/.test(q("#quotEnqServices").textContent), q("#quotEnqServices").textContent.slice(0, 160));
  const box = n => q(`[data-enq-svc="${n}"]`);
  check("customs clearance and transport are pre-ticked from the enquiry (clearance yes, delivery yes)", box("CHA").checked && box("Transport").checked && !box("Warehouse").checked);
  check("the quotation type follows: freight + clearance + transport = Combined, Next is ready", w.quotWiz.qtype === "Combined" && q("#quotWizNext").disabled === false && w.quotWiz.mode === "Sea" && w.quotWiz.dir === "Export" && w.quotWiz.type === "FCL", w.quotWiz.qtype);
  const tick = async (n, v) => { box(n).checked = v; box(n).dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(250); };
  await tick("Transport", false);
  check("only customs clearance: a custom combination of freight + CHA", w.quotWiz.qtype === "Custom" && w.quotWiz.customServices.CHA === true && w.quotWiz.customServices.Transport === false && w.quotWiz.customServices.Freight === true, JSON.stringify(w.quotWiz.customServices));
  await tick("CHA", false);
  check("nothing ticked: freight only", w.quotWiz.qtype === "Freight Only" && q("#quotWizNext").disabled === false, w.quotWiz.qtype);
  await tick("Warehouse", true);
  check("warehouse alone adds a custom combination", w.quotWiz.qtype === "Custom" && w.quotWiz.customServices.Warehouse === true);
  await tick("Warehouse", false); await tick("CHA", true); await tick("Transport", true);
  check("the carrier setup defaults to a single carrier and can be changed", w.quotWiz.carrierMode === "single" && shown("#quotWizCarrierWrap"));
  q("#quotWizNext").click(); await sleep(1500);
  check("Continue opens the quotation form with the enquiry's shipment and the chosen services", q("#quotFormWrap").style.display === "block" && q("#quotWizWrap").style.display === "none" && w.quotWiz.qtype === "Combined" && w.quotWiz.dir === "Export", w.quotWiz.qtype);
  // Change shipment details brings the full wizard back
  w.go("quotation-new"); await sleep(2000);
  w.applyEnquiryToQuotWiz(enq); await sleep(500);
  q("#quotEnqChange").click(); await sleep(500);
  check("Change shipment details brings the mode, direction and type questions back", shown("#quotWizModeGrid") && !shown("#quotEnqServices") && /Select Quotation Details/.test(q("#quotWizTitle").textContent) && w.quotWiz.fromEnq === false);
  // a fresh quotation after that starts the full wizard again
  w.go("quotation"); await sleep(800); w.go("quotation-new"); await sleep(2000);
  check("a new quotation after an enquiry one starts with the full wizard", shown("#quotWizModeGrid") && !shown("#quotEnqServices") && w.quotWiz.fromEnq === false);
  // ---- the real path: a confirmed enquiry in the CRM -> Generate Quotation
  const saved = await j("PUT", "/api/crm/enquiry", { ref: "ENQ-2026-0900", version: 0, customer_id: null, company: "Real Path Exports", status: "Confirmed", data: { company: "Real Path Exports", shipmentType: "Sea Export FCL", modeOfTransport: "Sea", department: "Operations", status: "Confirmed", customsClearance: "No", pickup: "Yes", delivery: "No", origin: "Chennai", destination: "Singapore", commodity: "Tiles", packages: 5, grossWeight: "900" } }, token);
  check("setup: a confirmed Sea Export FCL enquiry is stored in the CRM", saved.status === 200, JSON.stringify(saved.body).slice(0, 160));
  w.go("dashboard"); await sleep(800); w.go("crm-enquiry"); await sleep(3000);
  const btn = q('[data-crm-quot-from-enq="ENQ-2026-0900"]');
  check("the enquiry list offers Generate Quotation for it", !!btn);
  if (btn) { btn.click(); await sleep(3500); }
  check("Generate Quotation lands on the services question, not on the full wizard", shown("#quotEnqServices") && !shown("#quotWizModeGrid") && !shown("#quotWizTypeWrap") && w.quotWiz.sourceEnquiry === "ENQ-2026-0900" && w.quotWiz.mode === "Sea" && w.quotWiz.dir === "Export" && w.quotWiz.type === "FCL", w.quotWiz.sourceEnquiry + " " + q("#quotWizTitle").textContent);
  check("transport is pre-ticked (pickup required), clearance is not", q('[data-enq-svc="Transport"]').checked && !q('[data-enq-svc="CHA"]').checked && w.quotWiz.qtype === "Custom" && w.quotWiz.customServices.Transport === true, JSON.stringify(w.quotWiz.customServices));
  q("#quotWizNext").click(); await sleep(1500);
  check("Continue opens the quotation prefilled from the enquiry", q("#quotFormWrap").style.display === "block" && /Singapore/.test(Array.from(d.querySelectorAll("[data-qh='pod']")).map(x => x.value).join(" ")), "");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
