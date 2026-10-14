// Headless UI check #6: Organization master (parties with roles, contacts, validation, edit, delete, role-filtered dropdowns).
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
  const org = await j("POST", "/api/organizations", { code: "OG" + tag.slice(-6).toUpperCase(), name: "Orgs " + tag, admin_email: `og${tag}@t.test`, admin_username: "og" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Og" }, dev.body.access_token);
  const login = await j("POST", "/api/auth/login", { identifier: "og" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  const token = login.body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const q = s => d.querySelector(s);
  const field = label => { const fg = Array.from(d.querySelectorAll("#s-org-new .fg")).find(x => { const l = x.querySelector("label.fl"); return l && l.textContent.replace(/\*/g, "").trim().toLowerCase().startsWith(label.toLowerCase()); }); return fg && fg.querySelector("input,select,textarea"); };
  const set = (label, v, ev) => { const el = field(label); el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const rows = () => Array.from(d.querySelectorAll("#orgTableBody tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());
  const chip = r => q(`#orgRoleFlags .fi[data-role="${r}"]`);

  w.go("org-list"); await sleep(800);
  check("organization list starts empty (no dummy rows)", rows().length === 1 && /No organizations yet/.test(rows()[0]), rows().join("|"));
  check("type filter lists real party types", Array.from(q("#orgTypeFilter").options).map(o => o.text).join(",") === "All Types,Client,Vendor,Carrier,CHA,Agent,Transporter");

  // ---- new client with role, tax ids, contact and address rows
  w.go("org-new"); await sleep(600);
  check("new organization form is blank", field("Org Code").value === "" && field("Full Legal Name").value === "" && q("#orgRoleFlags .fi.on") === null);
  q("#orgSaveBtn").click(); await sleep(300);
  check("empty form is blocked", toastsNow().some(t => /Required fields missing/.test(t)), toastsNow().join(";"));
  set("Org Code", "tatachem"); set("Full Legal Name", "Tata Chemicals Ltd"); set("Short Name", "Tata");
  set("GSTIN", "BAD"); set("PAN", "AAACT2727Q"); field("State").value = field("State").options[1].value; field("State").dispatchEvent(new w.Event("change", { bubbles: true }));
  set("Address Line 1", "Bombay House"); set("City", "Mumbai");
  chip("shipper").click();
  clearToasts(); q("#orgSaveBtn").click(); await sleep(1200);
  check("invalid GSTIN is rejected by the server and reported", toastsNow().some(t => /GSTIN format is invalid/i.test(t)), toastsNow().join(";"));
  set("GSTIN", "27AAACT2727Q1ZN");
  // contact row
  Array.from(d.querySelectorAll("#ot3 .ch button"))[1].click(); await sleep(200);
  const ci = d.querySelectorAll('#ot3 input[data-orr="contacts"]');
  ci[0].value = "Suresh Krishnan"; ci[0].dispatchEvent(new w.Event("input", { bubbles: true }));
  const email = Array.from(ci).find(i => i.dataset.k === "email"); email.value = "suresh@tata.test"; email.dispatchEvent(new w.Event("input", { bubbles: true }));
  Array.from(d.querySelectorAll("#ot2 .ch button")).filter(b => b.id !== "oxAddBranch").pop().click(); await sleep(200);
  const ai = Array.from(d.querySelectorAll('#ot2 input[data-orr="addresses"]')).find(i => i.dataset.k === "address"); ai.value = "Plot 14, Industrial Estate"; ai.dispatchEvent(new w.Event("input", { bubbles: true }));
  clearToasts(); q("#orgSaveBtn").click(); await sleep(1500);
  check("after creating, the form stays open on the saved organization (branches/credit now available)", q("#s-org-new").dataset.orgId !== "" && /Organization created/.test(toastsNow().join(";")), toastsNow().join(";"));
  w.go("org-list"); await sleep(900);
  const saved = (await j("GET", "/api/customers", null, token)).body;
  check("organization saved with all details in PostgreSQL", saved.length === 1 && saved[0].code === "TATACHEM" && saved[0].gstin === "27AAACT2727Q1ZN" && saved[0].roles.includes("shipper") && saved[0].data.city === "Mumbai" && saved[0].data.contacts[0].name === "Suresh Krishnan" && saved[0].data.addresses[0].address === "Plot 14, Industrial Estate", JSON.stringify(saved[0]).slice(0, 500) + toastsNow().join(";"));
  check("list shows it with role badge", rows().some(r => r.includes("TATACHEM") && r.includes("Tata Chemicals Ltd") && r.includes("Shipper")), rows().join("|"));

  // ---- vendor (carrier) with different role
  w.go("org-new"); await sleep(500);
  set("Org Code", "MSC"); set("Full Legal Name", "MSC Mediterranean Shipping"); field("Country").value = Array.from(field("Country").options).find(o => /Switzerland/.test(o.text)).value; field("Country").dispatchEvent(new w.Event("change", { bubbles: true }));
  set("Address Line 1", "12-14 Chemin Rieu"); set("City", "Geneva");
  chip("carrier").click(); chip("ap").click();
  q("#orgSaveBtn").click(); await sleep(1500);
  const all = (await j("GET", "/api/customers", null, token)).body;
  const msc = all.find(c => c.code === "MSC");
  check("carrier saved for real (no more fake toast) with country and roles", !!msc && msc.country === "Switzerland" && msc.roles.sort().join() === "ap,carrier", JSON.stringify(msc));

  // ---- filters
  w.go("org-list"); await sleep(800);
  q("#orgTypeFilter").value = "Carrier"; q("#orgTypeFilter").dispatchEvent(new w.Event("change", { bubbles: true }));
  check("type filter shows only carriers", rows().length === 1 && rows()[0].includes("MSC"), rows().join("|"));
  q("#orgTypeFilter").value = ""; q("#orgTypeFilter").dispatchEvent(new w.Event("change", { bubbles: true }));
  q("#orgSearch").value = "tata"; q("#orgSearch").dispatchEvent(new w.Event("input", { bubbles: true }));
  check("search filters the list", rows().length === 1 && rows()[0].includes("TATACHEM"), rows().join("|"));
  q("#orgSearch").value = ""; q("#orgSearch").dispatchEvent(new w.Event("input", { bubbles: true }));

  // ---- edit
  d.querySelector('[data-oe="' + saved[0].id + '"]').click(); await sleep(900);
  check("edit form is loaded from the database (fields, role, contact, locked code)", field("Full Legal Name").value === "Tata Chemicals Ltd" && field("GSTIN").value === "27AAACT2727Q1ZN" && field("City").value === "Mumbai" && chip("shipper").classList.contains("on") && field("Org Code").disabled === true && d.querySelectorAll('#ot3 input[data-orr="contacts"]')[0].value === "Suresh Krishnan");
  field("Full Legal Name").value = "Tata Chemicals Limited"; chip("consignee").click();
  q("#orgSaveBtn").click(); await sleep(1500);
  const edited = (await j("GET", `/api/customers/${saved[0].id}`, null, token)).body;
  check("edit persisted (name and added role)", edited.name === "Tata Chemicals Limited" && edited.roles.includes("consignee") && edited.data.contacts.length === 1, JSON.stringify(edited).slice(0, 300));

  // ---- master connectivity: dropdowns and billing parties by role
  w.go("job-new"); await sleep(900);
  const shipperOpts = Array.from(q("#nj_shipper").options).map(o => o.text).join("|");
  check("Job shipper dropdown lists clients only (carrier excluded)", /Tata Chemicals Limited/.test(shipperOpts) && !/MSC/.test(shipperOpts), shipperOpts);
  w.BK.openNew("purchase_invoice"); await sleep(1500);
  const vend = Array.from(d.querySelectorAll("#bkParties_purchase-invoice-new option")).map(o => o.value).join("|");
  check("Purchase invoice vendor list comes from vendor organizations", /MSC Mediterranean Shipping/.test(vend) && !/Tata/.test(vend), vend);
  w.BK.openNew("invoice"); await sleep(1500);
  const cust = Array.from(d.querySelectorAll("#bkParties_invoice-new option")).map(o => o.value).join("|");
  check("Invoice customer list comes from client organizations", /Tata Chemicals Limited/.test(cust) && !/MSC/.test(cust), cust);

  // ---- jobs screens load from the real API (these hooks were never exported before)
  const tata = (await j("GET", "/api/customers", null, token)).body.find(c => c.code === "TATACHEM");
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", customer_id: tata.id, consignee: "Solvay SA" }, token);
  w.go("job-list"); await sleep(1200);
  const jobRows = Array.from(d.querySelectorAll("#jobTableBody tr[data-job-row]")).map(r => r.textContent.replace(/\s+/g, " "));
  check("Jobs list loads real jobs from the database", jobRows.length === 1 && /Tata Chemicals Limited/.test(jobRows[0]) && /Solvay SA/.test(jobRows[0]), jobRows.join("|"));

  // ---- delete
  w.go("org-list"); await sleep(800);
  d.querySelector('[data-od="' + msc.id + '"]').click(); await sleep(1200);
  check("delete removes the organization from the database", !(await j("GET", "/api/customers", null, token)).body.some(c => c.code === "MSC"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
