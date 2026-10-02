// Headless UI check #2: departments, designations, employees, routes, charges (real API + QA database).
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
  const org = await j("POST", "/api/organizations", { code: "MO" + tag.slice(-6).toUpperCase(), name: "More " + tag, admin_email: `mo${tag}@t.test`, admin_username: "mo" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mo" }, dev.body.access_token);
  const login = await j("POST", "/api/auth/login", { identifier: "mo" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number });
  const token = login.body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true;
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.scrollTo = () => {};
      w.console.error = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const toasts = []; const orig = w.toast; w.toast = (a, b, c) => { toasts.push(a + " | " + b); try { orig && orig(a, b, c); } catch (e) {} };
  const field = (scr, label) => { const fg = Array.from(d.querySelectorAll("#s-" + scr + " .fg")).find(x => { const l = x.querySelector("label.fl"); return l && l.textContent.replace(/\*/g, "").trim().toLowerCase().startsWith(label.toLowerCase()); }); return fg && fg.querySelector("input,select,textarea"); };
  const rows = sel => Array.from(d.querySelectorAll(sel + " tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());
  const clickSave = (scr, id) => (id ? d.getElementById(id) : Array.from(d.querySelectorAll("#s-" + scr + " button")).find(b => /^\s*save/i.test(b.textContent))).click();

  // Department
  w.go("dept-list"); await sleep(500);
  check("department list has no dummy rows", !/Operations|Finance/.test(rows("#s-dept-list tbody").join("|")), rows("#s-dept-list tbody").join("|"));
  w.go("dept-new"); await sleep(400);
  field("dept-new", "Department Code").value = "OPS"; field("dept-new", "Department Name").value = "Operations"; field("dept-new", "Email").value = "ops@x.test";
  toasts.length = 0; clickSave("dept-new", "deptSaveBtn"); await sleep(1200);
  const depts = (await j("GET", "/api/masters/department", null, token)).body;
  check("department Save button persists to PostgreSQL", depts.length === 1 && depts[0].code === "OPS" && depts[0].data.email === "x@x" || (depts.length === 1 && depts[0].data.email === "ops@x.test"), JSON.stringify(depts) + toasts.join(";"));
  check("only one save toast (the fake stub is gone)", toasts.filter(t => /saved/i.test(t)).length === 1, toasts.join(";"));
  check("department list shows saved row", rows("#s-dept-list tbody").some(t => t.includes("OPS") && t.includes("Operations")), rows("#s-dept-list tbody").join("|"));

  // Designation fed by departments
  w.go("desig-new"); await sleep(800);
  const deptOpts = Array.from(field("desig-new", "Department").options).map(o => o.text);
  check("designation Department dropdown lists real departments only", deptOpts.includes("Operations") && !deptOpts.includes("Finance"), deptOpts.join(","));
  field("desig-new", "Designation Code").value = "OPSMGR"; field("desig-new", "Designation Name").value = "Operations Manager"; field("desig-new", "Department").value = "Operations";
  clickSave("desig-new", "desigSaveBtn"); await sleep(1000);
  const desigs = (await j("GET", "/api/masters/designation", null, token)).body;
  check("designation persisted with department", desigs.length === 1 && desigs[0].data.department === "Operations", JSON.stringify(desigs));

  // Employee
  w.go("emp-new"); await sleep(900);
  field("emp-new", "Employee Code").value = "EMP-1"; field("emp-new", "Employee Name").value = "Asha Rao"; field("emp-new", "Mobile").value = "9000000001";
  field("emp-new", "Official Email").value = "asha@x.test"; field("emp-new", "Joining Date").value = "2026-01-05";
  field("emp-new", "Department").value = "Operations"; field("emp-new", "Designation").value = "Operations Manager";
  clickSave("emp-new", "empSaveBtn"); await sleep(1200);
  const emps = (await j("GET", "/api/masters/employee", null, token)).body;
  check("employee persisted with department/designation", emps.length === 1 && emps[0].name === "Asha Rao" && emps[0].data.department === "Operations" && emps[0].data.mobile === "9000000001", JSON.stringify(emps) + toasts.slice(-2).join(";"));
  check("employee list shows the saved employee", rows("#empTableBody").some(t => t.includes("EMP-1") && t.includes("Asha Rao")), rows("#empTableBody").join("|"));
  w.go("emp-new"); await sleep(300); clickSave("emp-new", "empSaveBtn"); await sleep(300);
  check("employee form blocks missing required fields", toasts.some(t => /Required fields missing/.test(t)));

  // Route
  w.go("route-new"); await sleep(400);
  field("route-new", "Route Code").value = "MAA-ANR"; field("route-new", "Route Name").value = "Chennai to Antwerp"; field("route-new", "Origin").value = "INMAA"; field("route-new", "Destination").value = "BEANR";
  clickSave("route-new", "routeSaveBtn"); await sleep(1000);
  check("route persisted and listed", (await j("GET", "/api/masters/route", null, token)).body.length === 1 && rows("#routeTableBody").some(t => t.includes("MAA-ANR") && t.includes("BEANR")), rows("#routeTableBody").join("|"));

  // Charge Master: HSN mandatory, then save
  w.go("charge-master"); await sleep(500);
  check("charge list has no dummy rows", !/Ocean Freight|996512/.test(rows("#s-charge-master tbody").join("|")), rows("#s-charge-master tbody").join("|"));
  d.getElementById("newChargeBtn").click(); await sleep(800);
  check("charge modal warns when no HSN master exists", /No HSN\/SAC codes exist yet/.test(d.getElementById("modalBody").textContent) && d.getElementById("modalOvl").classList.contains("on"));
  w.closeModal(); await sleep(700);
  await j("POST", "/api/masters/hsn", { code: "996512", name: "Water transport services of goods", data: { gst_rate: "18" } }, token);
  d.getElementById("newChargeBtn").click(); await sleep(800);
  d.getElementById("nc_code").value = "OFR"; d.getElementById("nc_name").value = "Ocean Freight";
  toasts.length = 0; d.getElementById("nc_save").click(); await sleep(400);
  check("charge cannot be saved without HSN/SAC", toasts.some(t => /HSN\/SAC required/.test(t)) && (await j("GET", "/api/masters/charge", null, token)).body.length === 0, toasts.join(";"));
  d.getElementById("nc_hsn").value = "996512"; d.getElementById("nc_hsn").dispatchEvent(new w.Event("change"));
  check("GST% auto-fills from the HSN master", d.getElementById("nc_gst").value === "18", d.getElementById("nc_gst").value);
  d.getElementById("nc_save").click(); await sleep(1200);
  const charges = (await j("GET", "/api/masters/charge", null, token)).body;
  check("charge persisted with HSN link and GST", charges.length === 1 && charges[0].data.hsn_code === "996512" && charges[0].data.gst_percent === "18", JSON.stringify(charges));
  check("charge list shows it", rows("#s-charge-master tbody").some(t => t.includes("OFR") && t.includes("996512") && t.includes("18%")), rows("#s-charge-master tbody").join("|"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
