// Duplicate: the job detail page has a Duplicate button (right side of the page header) that opens a fresh Draft job with the next running number; quotations and bookings duplicate to new drafts.
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
  const org = await j("POST", "/api/organizations", { code: "DP" + tag.slice(-6).toUpperCase(), name: "Dup " + tag, admin_email: `dp${tag}@t.test`, admin_username: "dp" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Dee" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "dp" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token)).body;
  const quote = (await j("POST", "/api/quotations", { party_name: "Dup Co", mode: "Sea", lines: [{ side: "SELL", charge_name: "Ocean Freight", ccy: "INR", exch: "1", rate: "100", qty: "1", basis: "Per Shipment" }] }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(7000);
  w.go("job-detail", { jobNo: job.job_number }); await sleep(2500);
  const btn = d.getElementById("jobDuplicateBtn");
  check("the job page has a Duplicate button in the header actions (right side)", !!btn && !!btn.closest(".pa"));
  btn.click(); await sleep(3500);
  const jobs = (await j("GET", "/api/shipments", null, token)).body;
  check("Duplicate creates a new Draft job with the next running number", jobs.length === 2 && jobs.some(x => x.job_number !== job.job_number && x.job_stage === "Draft" && x.pol === "INMAA"), JSON.stringify(jobs.map(x => x.job_number)));
  const newNo = jobs.find(x => x.job_number !== job.job_number).job_number;
  check("the new job is opened", (d.getElementById("jobDetailNo") || {}).textContent.trim() === newNo, (d.getElementById("jobDetailNo") || {}).textContent);
  const qd = await j("POST", `/api/quotations/${quote.id}/duplicate`, null, token);
  check("a quotation duplicates to a new draft", qd.status === 201 && qd.body.status === "DRAFT" && qd.body.id !== quote.id);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
