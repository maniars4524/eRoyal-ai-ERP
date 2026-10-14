// Headless UI check #10: Organization Master enhancement (branches, branch contacts, COA/AP/AR classification, credit workflow).
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
  const org = await j("POST", "/api/organizations", { code: "OX" + tag.slice(-6).toUpperCase(), name: "OrgExt " + tag, admin_email: `ox${tag}@t.test`, admin_username: "ox" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Sales" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const token = (await j("POST", "/api/auth/login", { identifier: "ox" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  await j("POST", "/api/users", { username: "mg" + tag, email: `mg${tag}@t.test`, password: "Ui-Test-Passw0rd-1", first_name: "Manager", role: "ERP_ADMIN" }, token);
  const mgr = (await j("POST", "/api/auth/login", { identifier: "mg" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  await j("POST", "/api/masters/account", { code: "1100", name: "Sundry Debtors", data: {} }, token);
  await j("POST", "/api/masters/account", { code: "2100", name: "Sundry Creditors", data: {} }, token);
  const cust = (await j("POST", "/api/customers", { code: "ABCLOG", name: "ABC Logistics", roles: ["shipper"], country: "Germany", data: { address_line_1: "Hafen 1", city: "Hamburg" } }, token)).body;

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const tab = async id => { q(`[data-tab="${id}"]`).click(); await sleep(250); };
  const mf = (sel, attr, key) => q(`${sel} [${attr}="${key}"]`);

  w.go("org-list"); await sleep(1200);
  q(`[data-oe="${cust.id}"]`).click(); await sleep(1800);
  const tabs = Array.from(d.querySelectorAll('.tabs[data-tabgroup="org"] .tbb')).map(b => b.textContent.trim());
  check("tabs show the five sections with Basic Details", tabs[0] === "01 Basic Details" && /Address & Branches/.test(tabs[1]) && /Contact Persons/.test(tabs[2]) && /COA \/ AP \/ AR/.test(tabs[3]) && /Credit Control/.test(tabs[4]), tabs.join("|"));
  check("existing tabs are kept", tabs.includes("Role Defaults") && tabs.includes("Registration") && tabs.includes("Staff & Docs"));

  // ---------- branches
  await tab("ot2");
  q("#oxAddBranch").click(); await sleep(300);
  setf(mf("#modalBody", "data-bf", "code"), "maa"); setf(mf("#modalBody", "data-bf", "name"), "Chennai"); setf(mf("#modalBody", "data-bf", "billing_enabled"), "1");
  clearToasts(); q("#oxBranchSave").click(); await sleep(900);
  check("billing-enabled branch without address/tax id is refused", toastsNow().some(t => /billing-enabled branch needs/i.test(t)), toastsNow().join(";"));
  setf(mf("#modalBody", "data-bf", "city"), "Chennai"); setf(mf("#modalBody", "data-bf", "address"), "1 Mount Road"); setf(mf("#modalBody", "data-bf", "gstin"), "33AAACT2727Q1ZN"); setf(mf("#modalBody", "data-bf", "state"), "Tamil Nadu");
  q("#oxBranchSave").click(); await sleep(1000);
  q("#oxAddBranch").click(); await sleep(300);
  setf(mf("#modalBody", "data-bf", "code"), "cjb"); setf(mf("#modalBody", "data-bf", "name"), "Coimbatore"); setf(mf("#modalBody", "data-bf", "billing_enabled"), "0");
  q("#oxBranchSave").click(); await sleep(1000);
  const br = (await j("GET", `/api/customers/${cust.id}/branches`, null, token)).body;
  check("two branches saved with independent billing flags", br.length === 2 && br.find(b => b.code === "MAA").billing_enabled === true && br.find(b => b.code === "CJB").billing_enabled === false, JSON.stringify(br));
  check("branch table lists them", /MAA/.test(text("#oxBranches")) && /Coimbatore/.test(text("#oxBranches")) && /YES/.test(text("#oxBranches")) && /NO/.test(text("#oxBranches")));
  q(`[data-ox="branch-toggle:${br.find(b => b.code === "CJB").id}"]`).click(); await sleep(800);
  check("a branch can be deactivated (never deleted)", (await j("GET", `/api/customers/${cust.id}/branches`, null, token)).body.find(b => b.code === "CJB").status === "INACTIVE");

  // ---------- contacts
  await tab("ot3");
  q("#oxAddContact").click(); await sleep(300);
  const maa = br.find(b => b.code === "MAA");
  setf(mf("#modalBody", "data-cf", "branch_id"), String(maa.id)); setf(mf("#modalBody", "data-cf", "name"), "Anita Rao"); setf(mf("#modalBody", "data-cf", "department"), "Finance"); setf(mf("#modalBody", "data-cf", "is_billing"), "1"); setf(mf("#modalBody", "data-cf", "email"), "anita@abc.test");
  q("#oxContactSave").click(); await sleep(1000);
  const cs = (await j("GET", `/api/customers/${cust.id}/contacts?branch_id=${maa.id}`, null, token)).body;
  check("contact saved under the selected branch", cs.length === 1 && cs[0].name === "Anita Rao" && cs[0].is_billing === true);
  check("contact table shows the branch and billing role", /MAA/.test(text("#oxContacts")) && /Anita Rao/.test(text("#oxContacts")) && /Billing/.test(text("#oxContacts")));

  // ---------- accounting classification
  await tab("ot6");
  setf(mf("#oxAcct", "data-ax", "acct_class"), "customer");
  check("Customer shows AR mapping only", /AR Control Account/.test(text("#oxAcct")) && !/AP Control Account/.test(text("#oxAcct")));
  setf(mf("#oxAcct", "data-ax", "acct_class"), "vendor");
  check("Vendor shows AP mapping only", /AP Control Account/.test(text("#oxAcct")) && !/AR Control Account/.test(text("#oxAcct")));
  setf(mf("#oxAcct", "data-ax", "acct_class"), "both");
  check("Both shows AR and AP sections", /AR Control Account/.test(text("#oxAcct")) && /AP Control Account/.test(text("#oxAcct")));
  check("control accounts come from the Chart of Accounts master", /1100 - Sundry Debtors/.test(d.querySelector('#oxAcct [data-ax="ar_account"]').innerHTML) && /2100 - Sundry Creditors/.test(d.querySelector('#oxAcct [data-ax="ap_account"]').innerHTML));
  setf(mf("#oxAcct", "data-ax", "ar_account"), "1100"); setf(mf("#oxAcct", "data-ax", "ap_account"), "2100"); setf(mf("#oxAcct", "data-ax", "credit_customer"), "yes");
  clearToasts(); q("#orgSaveBtn").click(); await sleep(400);
  await sleep(1200);
  let saved = (await j("GET", `/api/customers/${cust.id}`, null, token)).body;
  check("classification and credit flag saved on the organization", saved.data.acct_class === "both" && saved.data.ar_account === "1100" && saved.data.ap_account === "2100" && saved.data.credit_customer === "yes" && saved.roles.includes("ar") && saved.roles.includes("ap"), JSON.stringify(saved.data));

  // ---------- credit
  w.go("org-list"); await sleep(1000); q(`[data-oe="${cust.id}"]`).click(); await sleep(1800);
  await tab("ot8");
  check("credit tab offers an application for a credit customer", !!q('[data-ox="apply:NEW"]'), text("#oxCredit").slice(0, 200));
  q('[data-ox="apply:NEW"]').click(); await sleep(1200);
  setf(mf("#oxCredit", "data-ap", "requested_limit"), "1000000"); setf(mf("#oxCredit", "data-ap", "credit_days"), "30");
  clearToasts(); q(`[data-ox^="app-submit:"]`).click(); await sleep(1200);
  check("submitting without an agreement number is refused", toastsNow().some(t => /Agreement number is required/i.test(t)), toastsNow().join(";"));
  setf(mf("#oxCredit", "data-ap", "agreement_no"), "AGR-2026-01");
  q(`[data-ox^="app-submit:"]`).click(); await sleep(1500);
  let cr = (await j("GET", `/api/customers/${cust.id}/credit`, null, token)).body;
  check("application is SUBMITTED for management approval", cr.facilities[0].status === "SUBMITTED", JSON.stringify(cr.facilities[0]));
  const own = await j("POST", `/api/customers/${cust.id}/credit/facilities/${cr.facilities[0].id}/approve`, { expiry_date: "2027-12-31" }, token);
  check("the requester cannot approve their own application", own.status === 403);
  const ok = await j("POST", `/api/customers/${cust.id}/credit/facilities/${cr.facilities[0].id}/approve`, { approved_limit: "1000000", expiry_date: new Date(Date.now() + 300 * 864e5).toISOString().slice(0, 10), policy: "SOFT", remarks: "Approved" }, mgr);
  check("manager approval makes the facility effective", ok.status === 200 && ok.body.status === "APPROVED");
  await tab("ot8"); w.go("org-list"); await sleep(800); q(`[data-oe="${cust.id}"]`).click(); await sleep(1800); await tab("ot8");
  check("credit position shows the approved limit and available credit", /10,00,000\.00/.test(text("#oxCredit")) && /Within limit/.test(text("#oxCredit")) && /Approved/.test(text("#oxCredit")), text("#oxCredit").slice(0, 400));
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", customer_id: cust.id, revenue: 900000 }, token);
  const blocked = await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", customer_id: cust.id, revenue: 500000 }, token);
  check("exceeding the limit is blocked by the backend (soft block)", blocked.status === 409 && /CREDIT_BLOCK/.test(blocked.body.detail), JSON.stringify(blocked.body));
  w.go("org-list"); await sleep(800); q(`[data-oe="${cust.id}"]`).click(); await sleep(1800); await tab("ot8");
  check("UI shows the limit exceeded state and the override option", /Limit exceeded|exceeded/i.test(text("#oxCredit")) && !!q('[data-ox="req:OVERRIDE"]'), text("#oxCredit").slice(0, 300));
  q('[data-ox="req:OVERRIDE"]').click(); await sleep(300);
  setf(mf("#modalBody", "data-rq", "reason"), "Key account urgent shipment");
  q("#oxReqSave").click(); await sleep(1200);
  cr = (await j("GET", `/api/customers/${cust.id}/credit`, null, token)).body;
  check("override request is pending management approval", cr.requests.length === 1 && cr.requests[0].kind === "OVERRIDE" && cr.requests[0].status === "PENDING");
  check("request cannot be self-approved", (await j("POST", `/api/customers/${cust.id}/credit/requests/${cr.requests[0].id}/approve`, {}, token)).status === 403);
  await j("POST", `/api/customers/${cust.id}/credit/requests/${cr.requests[0].id}/approve`, { valid_until: new Date(Date.now() + 5 * 864e5).toISOString().slice(0, 10) }, mgr);
  const after = await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", customer_id: cust.id, revenue: 500000 }, token);
  check("approved override lets the shipment proceed", after.status === 201);
  check("approval history and audit trail are recorded", cr.events.some(e => e.action === "APPLICATION_APPROVED") && cr.events.some(e => e.action === "ACTION_BLOCKED"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
