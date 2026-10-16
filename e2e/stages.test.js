// Headless UI check: simplified job stages, who/when, Source information pop-up, close (alert only when activities are pending), reopen, success / error toasts.
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
  const org = await j("POST", "/api/organizations", { code: "SG" + tag.slice(-6).toUpperCase(), name: "Stages " + tag, admin_email: `sg${tag}@t.test`, admin_username: "sg" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Stella", admin_last_name: "Admin" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sg" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => ((q$(s) || {}).textContent || "").replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const btn = label => Array.from(d.querySelectorAll("#s-job-detail .pa button")).find(b => b.textContent.trim().startsWith(label));
  const toasts = () => Array.from(d.querySelectorAll("#toastStack .toast"));
  const clearToasts = () => toasts().forEach(t => t.remove());

  // ---- a job created directly
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body;
  w.go("job-detail", { jobNo: job.job_number }); await sleep(2500);
  check("the job starts as Draft, with the date and who did it", /STAGE\s*DRAFT/i.test(text("#jlChips")) && /by Stella/.test(text("#jlChips")), text("#jlChips"));
  check("no old stage names or Closure chip are shown", !/Job Created|Booking Confirmed|CLOSURE/.test(text("#jlChips")));
  const steps = () => Array.from(d.querySelectorAll("#s-job-detail .sf .sfn")).map(n => n.textContent.trim() + (n.classList.contains("act") ? "*" : n.classList.contains("done") ? "+" : ""));
  check("the progress strip is Draft > Confirmed > On Transit > Closed > Re-open with Draft active", steps().join(",") === "Draft*,Confirmed,On Transit,Closed,Re-open", steps().join(","));
  check("a Source information link sits under the job", !!q$("[data-jl-source]"));
  q$("[data-jl-source]").click(); await sleep(1200);
  check("Source information pops up: created directly, by whom", /Source information/.test(text("#modalTitle")) && /directly/.test(text("#modalBody")) && /Stella/.test(text("#modalBody")), text("#modalBody").slice(0, 200));
  w.closeModal();

  // ---- changing the stage
  btn("Change Stage").click(); await sleep(500);
  check("the stage dialog offers Draft, Confirmed and On Transit only", Array.from(d.querySelectorAll("#jlNewStage option")).map(o => o.value).join(",") === "Draft,Confirmed,On Transit");
  setf(q$("#jlNewStage"), "Confirmed", "change"); setf(q$("#jlStageWhy"), "Customer confirmed the shipment");
  clearToasts(); q$("#jlStageSave").click(); await sleep(1500);
  check("the stage becomes Confirmed and the strip follows", /STAGE\s*CONFIRMED/i.test(text("#jlChips")) && steps().join(",") === "Draft+,Confirmed*,On Transit,Closed,Re-open", text("#jlChips") + " | " + steps().join(","));
  const ok = toasts().find(t => /Job Stage Updated Successfully!/.test(t.textContent));
  check("a green success toast with a check icon appears after the save", !!ok && ok.classList.contains("success") && !!ok.querySelector('svg[data-icon="check-circle"]'), toasts().map(t => t.className + ":" + t.textContent).join("|"));

  // ---- closing with activities pending: an alert; nothing is closed
  btn("Close Job").click(); await sleep(1800);
  check("Close Job with pending activities shows the alert listing them", /Pending activities/.test(text("#modalTitle")) && /PENDING/.test(text("#modalBody")), text("#modalTitle"));
  check("and the job is still open", (await j("GET", `/api/jobs/${job.job_number}/lifecycle`, null, token)).body.closure_status === "OPEN");
  w.closeModal();

  // ---- complete every activity, then closing needs no pop-up
  await j("PUT", `/api/shipments/by-number/${job.job_number}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "9965", buy: { ccy: "INR", rate: "6000", tax_type: "Non-Taxable", party: "MSC" }, sell: { ccy: "INR", rate: "10000", tax_type: "Taxable", tax_pct: "18", party: "Acme" } }] }, token);
  const ship = (await j("GET", "/api/shipments", null, token)).body.find(x => x.job_number === job.job_number);
  await j("PATCH", `/api/shipments/${ship.id}`, { bl_awb: "MAEU123456" }, token);
  for (const code of ["documentation_completed", "cargo_received", "customs_cleared", "departed"]) await j("PUT", `/api/jobs/${job.job_number}/milestones/${code}`, { actual_at: new Date().toISOString() }, token);
  const inv = await j("POST", "/api/documents/from-job", { job_number: job.job_number, doc_type: "invoice" }, token); await j("POST", `/api/documents/${inv.body.id}/post`, null, token);
  const pinv = await j("POST", "/api/documents/from-job", { job_number: job.job_number, doc_type: "purchase_invoice" }, token); if (pinv.body && pinv.body.id) await j("POST", `/api/documents/${pinv.body.id}/post`, null, token);
  w.go("job-detail", { jobNo: job.job_number }); await sleep(2500);
  check("the departed job shows On Transit", /STAGE\s*ON TRANSIT/i.test(text("#jlChips")), text("#jlChips"));
  clearToasts(); btn("Close Job").click(); await sleep(2500);
  const lc = (await j("GET", `/api/jobs/${job.job_number}/lifecycle`, null, token)).body;
  check("with every activity done, Close Job closes straight away (no pop-up)", lc.closure_status === "CLOSED" && lc.job_stage === "Closed" && !d.querySelector("#modalOvl.on"), JSON.stringify([lc.closure_status, lc.job_stage]));
  check("the Closed stage shows, with a green success toast", /STAGE\s*CLOSED/i.test(text("#jlChips")) && toasts().some(t => t.classList.contains("success") && /Job Closed Successfully!/.test(t.textContent)), toasts().map(t => t.textContent).join("|"));

  // ---- reopen
  btn("Reopen Job").click(); await sleep(1500);
  clearToasts(); q$("#jlReopenGo").click(); await sleep(2000);
  check("Reopen puts the job in the Reopened stage", /STAGE\s*REOPENED/i.test(text("#jlChips")) && steps().join(",").startsWith("Draft+,Confirmed+,On Transit+,Closed+,Re-open*"), text("#jlChips") + " | " + steps().join(","));
  check("Change Stage is not offered while the job is Reopened", !btn("Change Stage"));

  // ---- a job raised from a quotation and booking: Source information names both
  const cust = (await j("POST", "/api/customers", { code: "AC" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper", "consignee"], data: {} }, token)).body;
  const ln = (r, s) => ({ side: s, charge_name: "Ocean Freight", carrier: "", basis: "Per Container", ccy: "USD", exch: "83.5", rate: r, qty: "2", tax_pct: "0" });
  const q = (await j("POST", "/api/quotations", { customer_id: cust.id, party_name: "Acme Exports", mode: "Sea", direction: "Export", shipment_type: "FCL", pol: "INMAA", pod: "BEANR", lines: [ln("850", "SELL"), ln("700", "BUY")] }, token)).body;
  await j("POST", `/api/quotations/${q.id}/approval/submit`, null, token); const apq = (await j("POST", `/api/quotations/${q.id}/approval/approve`, {}, token)).body;
  await j("POST", `/api/quotations/${q.id}/send`, null, token); await j("POST", `/api/quotations/${q.id}/accept`, null, token);
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", customer_id: cust.id, party_name: "Acme Exports", carrier: "MSC", pol: "INMAA", pod: "BEANR", quotation_id: q.id, data: { services: "Freight", pcs: "10" } }, token)).body;
  const cb = (await j("POST", `/api/bookings/${bk.id}/confirm`, null, token)).body;
  const bj = cb.job_number;
  w.go("job-detail", { jobNo: bj }); await sleep(2500);
  check("a job from a booking starts as Confirmed", /STAGE\s*CONFIRMED/i.test(text("#jlChips")), text("#jlChips"));
  q$("[data-jl-source]").click(); await sleep(1200);
  check("Source information names the booking and the quotation", new RegExp(cb.number.replace(/\//g, "\\/")).test(text("#modalBody")) && new RegExp(apq.number.replace(/\//g, "\\/")).test(text("#modalBody")) && /Acme Exports/.test(text("#modalBody")), text("#modalBody").slice(0, 300));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
