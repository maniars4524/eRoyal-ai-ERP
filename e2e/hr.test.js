// Headless UI check: HR masters. Nothing is pre-filled; the company's designation list is imported (code, designation, department, level); the staff form picks designations, reporting managers and branches from the masters.
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
const LIST = `Code\tDesignation\tDepartment\tLevel
DES001\tManaging Director\tManagement\tL1
DES002\tGeneral Manager\tAdministration\tL2
DES003\tHR Manager\tHR\tL3
DES004\tHR Executive\tHR\tL4
DES005\tProduction Manager\tProduction\tL3
DES006\tProduction Supervisor\tProduction\tL4
DES007\tQuality Engineer\tQuality\tL4
DES008\tMaintenance Engineer\tMaintenance\tL4
DES009\tStorekeeper\tStores\tL5
DES010\tMachine Operator\tProduction\tL6
DES011\tAccountant\tFinance\tL4
DES012\tSales Executive\tSales\tL4`;
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "HR" + tag.slice(-5).toUpperCase(), name: "Hr " + tag, admin_email: `hr${tag}@t.test`, admin_username: "hr" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "H" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "hr" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500);
  w.go("desig-list"); await sleep(2000);
  check("the Designation master starts empty (no made-up designations)", qa("#desigTableBody tr").filter(r => !/colspan/.test(r.innerHTML)).length === 0 && /Designations \(0\)/.test(q("#desigCount").textContent) && (await j("GET", "/api/masters/designation", null, token)).body.length === 0, qa("#desigTableBody tr").map(r => r.textContent.trim()).join("|") + " " + q("#desigCount").textContent);
  w.go("dept-list"); await sleep(1500);
  check("and so does the Department master", qa("#s-dept-list tbody tr").filter(r => !/No records|colspan/.test(r.innerHTML)).length === 0);
  w.go("desig-list"); await sleep(800);
  q("#desigImportBtn").click(); await sleep(500);
  q("#di_text").value = LIST; q("#di_check").click(); await sleep(1500);
  check("the pasted list is checked first: 12 new, departments to be created", /12 new/.test(q("#di_out").textContent) && /Management/.test(q("#di_out").textContent) && /Production/.test(q("#di_out").textContent) && q("#di_go").disabled === false, q("#di_out").textContent.slice(0, 220));
  check("nothing is saved by checking", (await j("GET", "/api/masters/designation", null, token)).body.length === 0);
  q("#di_go").click(); await sleep(2500);
  const rows = qa("#desigTableBody tr").map(t => t.textContent.replace(/\s+/g, " ").trim());
  check("the designations are loaded with department and level", rows.length === 12 && rows.some(r => /DES001.*Managing Director.*Management.*L1/.test(r)) && rows.some(r => /DES010.*Machine Operator.*Production.*L6/.test(r)), rows.slice(0, 2).join(" | "));
  check("the count and the department filter follow the master", /Designations \(12\)/.test(q("#desigCount").textContent) && Array.from(q("#desigDeptFilter").options).map(o => o.value).filter(Boolean).sort().join() === "Administration,Finance,HR,Maintenance,Management,Production,Quality,Sales,Stores", q("#desigCount").textContent);
  check("the departments of the list were created in the Department master", (await j("GET", "/api/masters/department", null, token)).body.length === 9);
  w.go("desig-new"); await sleep(1500);
  check("the designation form has Level and Grade beside the department, and Reports To", !!q("#desig_level") && !!q("#desig_grade") && Array.from(q("#desig_reports").options).length === 13, Array.from(q("#desig_reports").options).length + "");
  // ---- staff form
  await j("POST", "/api/masters/branch", { code: "BR1", name: "Head Office", data: {} }, token);
  await j("POST", "/api/masters/employee", { code: "E001", name: "Asha Rao", data: { department: "HR", designation: "HR Manager" } }, token);
  w.go("emp-list"); await sleep(1500); w.go("emp-new"); await sleep(3800);
  const opts = id => Array.from(q(id).options).map(o => o.value).filter(Boolean);
  const lab = t => Array.from(d.querySelectorAll("#s-emp-new label.fl")).find(l => l.textContent.trim().startsWith(t)).parentElement.querySelector("select,input");
  check("the staff form's Designation list is the company's own list", opts("#emp_desig").length === 12 && opts("#emp_desig").includes("Storekeeper"), opts("#emp_desig").length + "");
  const dep = q("#emp_dept"); dep.value = "HR"; dep.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1200);
  check("choosing a department narrows the designations to it", opts("#emp_desig").join() === "HR Manager,HR Executive" || opts("#emp_desig").sort().join() === "HR Executive,HR Manager", opts("#emp_desig").join("|"));
  check("Reporting Manager lists the employees of the master (no made-up names)", Array.from(lab("Reporting Manager").options).map(o => o.value).filter(Boolean).join() === "Asha Rao", Array.from(lab("Reporting Manager").options).map(o => o.value).join("|"));
  check("Work Location / Branch lists the branches of the master", Array.from(lab("Work Location").options).map(o => o.value).filter(Boolean).join() === "Head Office");
  // ---- the extra HR details are saved
  const set = (el, v) => { el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); el.dispatchEvent(new w.Event("change", { bubbles: true })); };
  check("the Employee Code is generated for a new employee and cannot be typed over", q("#emp_code").value === "EMP-0001" && q("#emp_code").readOnly === true, q("#emp_code").value);
  set(q("#emp_name"), "Ravi Kumar"); set(q("#emp_join"), "2026-06-01"); set(q("#emp_mobile"), "9876543210"); set(q("#emp_email"), "ravi@example.com");
  set(q("#emp_dept"), "HR"); await sleep(900); set(q("#emp_desig"), "HR Executive");
  set(lab("Reporting Manager"), "Asha Rao"); set(lab("Work Location"), "Head Office"); set(lab("Probation Period"), "6"); set(lab("Leaving Reason"), "n/a"); set(lab("Passport Number"), "P1234567"); set(lab("Driving Licence"), "TN0120260001");
  await w.MK.save(w.MK.configs.employee); await sleep(2000);
  const emp = (await j("GET", "/api/masters/employee", null, token)).body.find(e => e.name === "Ravi Kumar");
  check("saving without typing a code works and the employee gets EMP-0001", !!emp && emp.code === "EMP-0001", emp && emp.code);
  check("the employee is saved with the department, designation, manager, branch and the extra HR details", !!emp && emp.data.designation === "HR Executive" && emp.data.reporting_manager === "Asha Rao" && emp.data.work_location_branch === "Head Office" && emp.data.probation_period_months === "6" && emp.data.passport_number === "P1234567" && emp.data.driving_licence_number === "TN0120260001", JSON.stringify(emp && emp.data).slice(0, 300));
  w.go("emp-list"); await sleep(1200); w.go("emp-new"); await sleep(3500);
  check("the next new employee is offered EMP-0002", q("#emp_code").value === "EMP-0002", q("#emp_code").value);
  set(q("#emp_name"), "Meena S"); set(q("#emp_join"), "2026-07-01"); set(q("#emp_dept"), "HR"); await sleep(900); set(q("#emp_desig"), "HR Executive"); set(q("#emp_mobile"), "9000000001"); set(q("#emp_email"), "meena@example.com");
  await w.MK.save(w.MK.configs.employee); await sleep(1800);
  check("and it is saved as EMP-0002", ((await j("GET", "/api/masters/employee", null, token)).body.find(e => e.name === "Meena S") || {}).code === "EMP-0002");
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
