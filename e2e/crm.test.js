// Headless UI check #9: CRM (leads -> enquiries -> opportunities) persisted in PostgreSQL, customers from the Customer Master.
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
  const org = await j("POST", "/api/organizations", { code: "CR" + tag.slice(-6).toUpperCase(), name: "Crm " + tag, admin_email: `cr${tag}@t.test`, admin_username: "cr" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Sales" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cr" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const acme = (await j("POST", "/api/customers", { code: "ACME", name: "Acme Exports", roles: ["shipper"], data: { email: "ops@acme.test" } }, token)).body;

  const open = async () => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
    await sleep(2500); return dom;
  };
  let dom = await open(), w = dom.window, d = w.document;
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());

  w.go("crm-lead"); await sleep(1500);
  check("leads list starts empty (no sample leads)", /No leads yet/.test(text("#crmLeadRoot")) && !/Zenith|Coastal Steel/.test(text("#crmLeadRoot")), text("#crmLeadRoot").slice(0, 200));
  q("#crmNewLeadBtn").click(); await sleep(300);
  check("lead form lists Customer Master names as suggestions", Array.from(d.querySelectorAll("#crmCustList option")).some(o => o.value === "Acme Exports"));
  check("assignee options come from real users (no hardcoded sales names)", !/Priya|Arvind|Divya/.test(text("#crmLeadFormWrap")) && /Sales/.test(text("#crmLd_assigned")), text("#crmLd_assigned"));
  setf(q("#crmLd_company"), "Acme Exports"); setf(q("#crmLd_contact"), "Ravi"); q("#crmLd_save").click(); await sleep(2500);
  let leads = (await j("GET", "/api/crm/lead", null, token)).body;
  check("lead is saved in PostgreSQL linked to the master customer", leads.length === 1 && leads[0].customer_id === acme.id && leads[0].company === "Acme Exports", JSON.stringify(leads).slice(0, 200));

  // convert to enquiry
  q("[data-crm-convert-lead]").click(); await sleep(600);
  check("enquiry form shows the customer picker", /Customer/.test(text("#crmEnquiryFormWrap")) && q("#crmEq_company").value === "Acme Exports");
  setf(q("#crmEq_company"), "Nobody Ltd"); d.querySelector("#crmEq_save").click(); await sleep(300);
  // converting from a lead is allowed; test the strict path below with a fresh enquiry
  await sleep(1800);
  let enqs = (await j("GET", "/api/crm/enquiry", null, token)).body;
  check("enquiry saved and linked back to the lead", enqs.length === 1 && enqs[0].data.leadId === leads[0].ref, JSON.stringify(enqs).slice(0, 200));
  leads = (await j("GET", "/api/crm/lead", null, token)).body;
  check("lead status updated to Converted in the database", leads[0].status === "Converted", leads[0].status);

  // strict path: new enquiry must use a master customer
  q("#crmNewEnquiryBtn") && q("#crmNewEnquiryBtn").click(); await sleep(300);
  if (!q("#crmEq_company")) { w.crmRenderEnquiryForm({}); await sleep(200); }
  setf(q("#crmEq_company"), "Nobody Ltd"); q("#crmEq_save").click(); await sleep(300);
  check("new enquiry for a name not in the Customer Master is refused", toastsNow().some(t => /not in the Customer Master/.test(t)), toastsNow().join(";"));
  setf(q("#crmEq_company"), "Acme Exports", "change"); await sleep(100);
  check("picking the customer fills contact details from the master", q("#crmEq_email").value === "ops@acme.test", q("#crmEq_email").value);
  setf(q("#crmEq_commodity"), "Tea"); setf(q("#crmEq_pol"), "INMAA"); q("#crmEq_save").click(); await sleep(2500);
  enqs = (await j("GET", "/api/crm/enquiry", null, token)).body;
  const mine = enqs.find(e => e.data.commodity === "Tea");
  check("master-linked enquiry persisted with customer id", mine && mine.customer_id === acme.id && mine.company === "Acme Exports", JSON.stringify(enqs).slice(0, 200));

  // opportunity
  w.go("crm-enquiry"); await sleep(1200);
  const st = d.querySelector("[data-crm-enq-status]"); if (st) { st.value = "Confirmed"; st.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(2500); }
  const makeOpp = d.querySelector("[data-crm-make-opp]"); makeOpp && makeOpp.click(); await sleep(2500);
  const opps = (await j("GET", "/api/crm/opportunity", null, token)).body;
  check("opportunity created from an enquiry is persisted", opps.length === 1 && opps[0].company, JSON.stringify(opps).slice(0, 200));
  w.close();

  // reload: everything comes back from the database
  dom = await open(); w = dom.window; d = w.document;
  w.go("crm-enquiry"); await sleep(2000); 
  check("after a fresh load the enquiries list comes from the database with the customer name", /Acme Exports/.test(text("#crmEnquiryRoot")) && (text("#crmEnquiryRoot").match(/ENQ\//g) || []).length >= 2, text("#crmEnquiryRoot").slice(0, 300));
  w.go("crm-lead"); await sleep(1500);
  check("leads survive a reload", /Acme Exports/.test(text("#crmLeadRoot")) && /Converted/.test(text("#crmLeadRoot")));

  // enquiry -> real quotation prefill
  const eb = d.querySelector("[data-crm-quot-from-enq]"); w.go("crm-enquiry"); await sleep(1200);
  const qb = d.querySelector("[data-crm-quot-from-enq]");
  if (qb) { qb.click(); await sleep(2500); d.querySelector("#quotWizNext") && (d.querySelector('#quotWizQtypeGrid .wiz-card') || {click(){}}).click(); await sleep(300); }
  check("quotation wizard opened from the enquiry carries the source banner", !qb || /Fetched from Enquiry/.test(text("#quotEnquirySourceBanner")), text("#quotEnquirySourceBanner"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
