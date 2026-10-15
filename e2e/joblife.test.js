// Headless UI check #12: job stage, manual close / reopen and the right-side Job Activity History.
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
  const org = await j("POST", "/api/organizations", { code: "JL" + tag.slice(-6).toUpperCase(), name: "JobLife " + tag, admin_email: `jl${tag}@t.test`, admin_username: "jl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ops" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "ACME", name: "Acme Exports", roles: ["shipper"] }, token)).body;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", customer_id: cust.id }, token)).body;
  const jn = job.job_number;
  await j("PUT", `/api/shipments/by-number/${jn}/charges`, { lines: [{ charge_name: "Ocean Freight", qty: "1", hsn: "9965", buy: { ccy: "INR", rate: "6000", tax_type: "Non-Taxable", party: "MSC" }, sell: { ccy: "INR", rate: "10000", tax_type: "Taxable", tax_pct: "18", party: "Acme" } }] }, token);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = (m, d) => d; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.addEventListener("error", e => { if (process.env.DBG) console.log("JSERR", e.message); }); } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());

  // ---------- list columns + filters
  w.go("job-list"); await sleep(2500);
  const heads = Array.from(d.querySelectorAll("#jobTable thead th")).map(t => t.textContent.trim());
  check("job list keeps its columns and adds stage / closure / activity columns", heads.includes("Job No") && heads.includes("Status") && heads.includes("Job Stage") && heads.includes("Closure") && heads.includes("Last Activity") && heads.includes("Reopens"), heads.join("|"));
  const row = q("#jobTableBody tr[data-job-row]");
  check("each job row shows its stage and closure status", row && /DRAFT/i.test(row.textContent) && /OPEN/.test(row.textContent), row && row.textContent.slice(0, 200));
  setf(q("#jlListClosure"), "CLOSED", "change"); await sleep(200);
  check("closure filter hides open jobs", q("#jobTableBody tr[data-job-row]").classList.contains("jl-hide"));
  setf(q("#jlListClosure"), "", "change");

  // ---------- header
  w.go("job-detail", { jobNo: jn }); await sleep(2800);
  check("stage and closure status appear beside the job number, separately", /DRAFT/.test(text("#jlChips")) && /by /.test(text("#jlChips")) && /2026|20\d\d/.test(text("#jlChips")), text("#jlChips"));
  const btns = Array.from(d.querySelectorAll("#s-job-detail [data-jl]")).map(b => b.dataset.jl);
  check("header offers Change Stage, Close Job and Activity History (not Reopen while open)", btns.includes("stage") && btns.includes("close") && btns.includes("history") && !btns.includes("reopen"), btns.join());

  // ---------- milestones -> stage
  q('[data-tab="jt6"]').click(); await sleep(300);
  check("tracking tab lists the milestones of a Sea Export FCL job", /cargo received/i.test(text("#jlMilestones")) && /Departed/.test(text("#jlMilestones")), text("#jlMilestones").slice(0, 200));
  for (const code of ["documentation_completed", "cargo_received", "customs_cleared", "departed"]) { q(`[data-jl-ms="${code}"]`).click(); await sleep(250); q("#jlMsSave").click(); await sleep(1000); }
  check("confirming actual milestones moves the stage automatically", /ON TRANSIT/.test(text("#jlChips")), text("#jlChips"));

  // ---------- activity drawer
  q('#s-job-detail [data-jl="history"]').click(); await sleep(1300);
  check("activity history opens in a right-hand drawer without leaving the job", q("#jlDrawer").classList.contains("on") && q('[data-tab="jt6"]') !== null && /cargo received confirmed/i.test(text("#jlDBody")), text("#jlDBody").slice(0, 200));
  setf(q("#jlFcat"), "stage", "change"); await sleep(1000);
  check("category filter shows only stage changes", /Job stage changed/.test(text("#jlDBody")) && !/confirmed/.test(text("#jlDBody")), text("#jlDBody").slice(0, 200));
  setf(q("#jlFcat"), "", "change"); setf(q("#jlFq"), "Documentation"); await sleep(1300);
  check("search finds the matching events", /Documentation/.test(text("#jlDBody")) && /Showing \d+ of \d+/.test(text("#jlDCount")), text("#jlDBody").slice(0, 150));
  setf(q("#jlFq"), ""); await sleep(1000);
  q('[data-jl-tab="stages"]').click(); await sleep(1000);
  check("stage history tab lists transitions with their trigger", /Draft/.test(text("#jlDBody")) && /MILESTONE/.test(text("#jlDBody")), text("#jlDBody").slice(0, 200));
  q("#jlDClose").click();
  check("closing the drawer keeps the job screen as it was", !q("#jlDrawer").classList.contains("on") && q("#s-job-detail").classList.contains("on"));

  // ---------- blocked close, then a proper one
  await j("PATCH", `/api/shipments/${job.id}`, { bl_awb: "MAEU123456" }, token);
  q('#s-job-detail [data-jl="close"]').click(); await sleep(1800);
  check("closure dialog shows the grouped checklist with the real figures", /A\. Documentation/.test(text("#modalBody")) && /F\. Closure Authorization/.test(text("#modalBody")) && /10,000\.00/.test(text("#modalBody")) && /4,000\.00/.test(text("#modalBody")), text("#modalBody").slice(0, 300));
  check("confirm stays disabled while mandatory items are pending", q("#jlConfirmClose").disabled === true);
  q("#modalFoot button").click(); await sleep(300);
  const inv = await j("POST", "/api/documents/from-job", { job_number: jn, doc_type: "invoice" }, token);
  await j("POST", `/api/documents/${inv.body.id}/post`, null, token);
  w.go("job-detail", { jobNo: jn }); await sleep(2500);
  check("with the invoice posted the job stays On Transit and is still open", /ON TRANSIT/.test(text("#jlChips")) && (await j("GET", `/api/jobs/${jn}/lifecycle`, null, token)).body.closure_status === "OPEN", text("#jlChips"));
  q('#s-job-detail [data-jl="close"]').click(); await sleep(1800);
  q('[data-jl-ack="cost_sheet_reviewed"]').click(); await sleep(1000);
  q('[data-jl-ack="accrual"]').click(); await sleep(1000);
  check("acknowledging the review and accrual enables confirmation", q("#jlConfirmClose").disabled === false);
  setf(q("#jlCloseRem"), "Final cost review completed");
  q("#jlConfirmClose").click(); await sleep(2200);
  check("job is CLOSED only after the explicit confirmation", /CLOSED/.test(text("#jlChips")) && !!q('#s-job-detail [data-jl="reopen"]') && !q('#s-job-detail [data-jl="close"]'), text("#jlChips"));
  const api1 = (await j("GET", `/api/jobs/${jn}/lifecycle`, null, token)).body;
  check("the database agrees (closure cycle 1, closer recorded)", api1.closure_status === "CLOSED" && api1.closure_cycle === 1 && !!api1.last_closed_by);
  check("a closed job is read-only on the API as well", (await j("PATCH", `/api/shipments/${job.id}`, { consignee: "x" }, token)).status === 409);

  // ---------- reopen and close again
  q('#s-job-detail [data-jl="reopen"]').click(); await sleep(1300);
  setf(q("#jlReopenWhy"), "Additional Vendor Invoice Received", "change"); q("#jlReopenGo").click(); await sleep(2000);
  check("reopening sets REOPENED and keeps the job number", /REOPENED/.test(text("#jlChips")) && text("#jobDetailNo") === jn, text("#jlChips"));
  q('#s-job-detail [data-jl="close"]').click(); await sleep(1800);
  q('[data-jl-ack="cost_sheet_reviewed"]').click(); await sleep(1000); q('[data-jl-ack="accrual"]').click(); await sleep(1000);
  q("#jlConfirmClose").click(); await sleep(2200);
  const api2 = (await j("GET", `/api/jobs/${jn}/lifecycle`, null, token)).body;
  check("a second closure creates cycle 2 and the first record survives", api2.closure_cycle === 2 && api2.reopen_count === 1 && (await j("GET", `/api/jobs/${jn}/closures`, null, token)).body.length === 3);
  q('#s-job-detail [data-jl="history"]').click(); await sleep(1400);
  check("drawer shows every closure and reopening event", (text("#jlDBody").match(/Job closed/g) || []).length >= 2 && /Job reopened/.test(text("#jlDBody")) && /Additional Vendor Invoice Received/.test(text("#jlDBody")), text("#jlDBody").slice(0, 300));
  q('[data-jl-tab="closures"]').click(); await sleep(1000);
  check("closure tab keeps both cycles", (text("#jlDBody").match(/cycle/g) || []).length >= 3, text("#jlDBody").slice(0, 200));
  check("export button is available to an authorised user", q("#jlDExport").style.display !== "none");
  q("#jlDClose").click();

  // ---------- manual stage change on another job
  const job2 = (await j("POST", "/api/shipments", { mode: "Air", direction: "Import", pol: "DEFRA", pod: "INMAA" }, token)).body;
  w.go("job-detail", { jobNo: job2.job_number }); await sleep(2800);
  q('#s-job-detail [data-jl="stage"]').click(); await sleep(700);
  setf(q("#jlNewStage"), "On Transit", "change"); clearToasts(); q("#jlStageSave").click(); await sleep(500);
  check("a stage change needs a reason", toastsNow().some(t => /Reason required/i.test(t)), toastsNow().join(";"));
  setf(q("#jlStageWhy"), "Customer confirmed the cargo is moving"); q("#jlStageSave").click(); await sleep(1600);
  check("the manual stage is applied and recorded as manual", /ON TRANSIT/.test(text("#jlChips")) && (await j("GET", `/api/jobs/${job2.job_number}/lifecycle`, null, token)).body.stage_source === "MANUAL", text("#jlChips"));
  const lc2 = (await j("GET", `/api/jobs/${job2.job_number}/lifecycle`, null, token)).body;
  check("no milestone was completed by the manual change", lc2.milestones.every(m => !m.done), JSON.stringify(lc2.milestones.filter(m => m.done)));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
