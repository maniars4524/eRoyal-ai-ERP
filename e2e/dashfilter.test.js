// Headless UI check: each cell of "Active Jobs - By Shipment Type" opens the job list showing exactly the jobs it counted.
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
  const org = await j("POST", "/api/organizations", { code: "DF" + tag.slice(-6).toUpperCase(), name: "DashFilter " + tag, admin_email: `df${tag}@t.test`, admin_username: "df" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "D" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "df" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const mk = async (b) => (await j("POST", "/api/shipments", b, token)).body;
  const A = await mk({ mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" });          // Draft: pending action
  const B = await mk({ mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "DEHAM" });          // confirmed: active, not pending
  await j("POST", `/api/jobs/${B.job_number}/stage`, { stage: "Confirmed", reason: "Customer confirmed" }, token);
  const C = await mk({ mode: "Air", direction: "Import", pol: "DEFRA", pod: "INMAA" });                                 // Draft air import
  const D = await mk({ mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN" });          // closed this month
  await j("PUT", `/api/shipments/by-number/${D.job_number}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "9965", buy: { ccy: "INR", rate: "6000", tax_type: "Non-Taxable", party: "MSC" }, sell: { ccy: "INR", rate: "10000", tax_type: "Taxable", tax_pct: "18", party: "Acme" } }] }, token);
  const sh = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === D.job_number);
  await j("PATCH", `/api/shipments/${sh.id}`, { bl_awb: "MAEU1" }, token);
  for (const c of ["documentation_completed", "cargo_received", "customs_cleared", "departed"]) await j("PUT", `/api/jobs/${D.job_number}/milestones/${c}`, { actual_at: new Date().toISOString() }, token);
  const inv = await j("POST", "/api/documents/from-job", { job_number: D.job_number, doc_type: "invoice" }, token); await j("POST", `/api/documents/${inv.body.id}/post`, null, token);
  const pinv = await j("POST", "/api/documents/from-job", { job_number: D.job_number, doc_type: "purchase_invoice" }, token); if (pinv.body && pinv.body.id) await j("POST", `/api/documents/${pinv.body.id}/post`, null, token);
  const closed = await j("POST", `/api/jobs/${D.job_number}/close`, { ack: { cost_sheet_reviewed: true, accrual: true } }, token);
  check("setup: one job is really closed", closed.status === 200 && closed.body.closure_status === "CLOSED", JSON.stringify(closed.body).slice(0, 150));

  const sum = (await j("GET", "/api/dashboard/summary", null, token)).body.jobs;
  check("the dashboard counts closed jobs as closed (not active) and Draft jobs as pending", sum.matrix["Sea Export"].active === 2 && sum.matrix["Sea Export"].pending === 1 && sum.matrix["Sea Export"].closed_mtd === 1 && sum.matrix["Air Import"].pending === 1 && sum.active === 3, JSON.stringify(sum.matrix));

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(3000); w.go("dashboard"); await sleep(2500);
  const shown = () => Array.from(d.querySelectorAll("#jobTableBody tr[data-job-row]")).filter(r => r.style.display !== "none").map(r => r.querySelector("td .mono").textContent.trim()).sort();
  const click = async (f) => { w.go("dashboard"); await sleep(900); d.querySelector(`#kpiDetailPanel .dd-cell[data-filter="${f}"]`).click(); await sleep(2200); };
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b.slice().sort());

  await click("sea-export-pending");
  check("Sea Export · Pending Action shows only the Draft sea-export job, not every job", eq(shown(), [A.job_number]) && /Pending Action/.test(d.getElementById("jobFilterLabel").textContent), shown().join(",") + " | " + d.getElementById("jobFilterLabel").textContent);
  await click("sea-export-active");
  check("Sea Export · Active shows the two open sea-export jobs (the closed one is not active)", eq(shown(), [A.job_number, B.job_number]), shown().join(","));
  await click("sea-export-closed");
  check("Sea Export · Closed (MTD) shows the job closed this month", eq(shown(), [D.job_number]), shown().join(","));
  await click("air-import-pending");
  check("Air Import · Pending Action shows the air import job", eq(shown(), [C.job_number]), shown().join(","));
  await click("sea-import-pending");
  check("a type with nothing pending shows an empty list", shown().length === 0, shown().join(","));
  check("the footer states what it shows", /Showing 0 of 4 jobs/.test(d.getElementById("jobListFooter").textContent), d.getElementById("jobListFooter").textContent);
  d.getElementById("jobFilterClearBtn").click(); await sleep(400);
  check("Clear Filter brings all four jobs back", eq(shown(), [A.job_number, B.job_number, C.job_number, D.job_number]) && d.getElementById("jobFilterBanner").style.display === "none", shown().join(","));
  d.getElementById("jobStateFilter").value = "pending"; d.getElementById("jobStateFilter").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("the job list also has its own Active / Pending / Closed filter", eq(shown(), [A.job_number, C.job_number]), shown().join(","));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
