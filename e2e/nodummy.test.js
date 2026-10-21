// Headless UI check: no sample documents or sample records anywhere; documents wait for the company's format and show only the job's own data.
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
const DUMMY = /PAS Freight|PAS FREIGHT|RLPL|Tata Chem|GEA West|Infosys|Ashok Leyland|TVS Motor|Raghav|Sigma Jones|J0191|J0728|PASFE|PROTOTYPE|Maersk Line|Sample/i;

(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "ND" + tag.slice(-6).toUpperCase(), name: "NoDummy " + tag, admin_email: `nd${tag}@t.test`, admin_username: "nd" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "N" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nd" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const cust = (await j("POST", "/api/customers", { code: "REAL" + tag.slice(-3).toUpperCase(), name: "Real Exports Pvt Ltd", roles: ["shipper", "consignee"], data: {} }, token)).body;
  const job = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR", customer_id: cust.id, consignee: "Real Consignee GmbH", packages: 12 }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const text = s => ((d.querySelector(s) || {}).textContent || "").replace(/\s+/g, " ");

  // job context, then every document screen
  w.go("job-detail", { jobNo: job.job_number }); await sleep(2500);
  for (const [screen, zone, name] of [["bl-draft", "blDraftPrintZone", "Bill of Lading / AWB"], ["freight-cert", "fcPrintZone", "Freight Certificate"], ["cargo-arrival", "canPrintZone", "Cargo Arrival Notice"], ["job-pl-report", "plPrintZone", "Job Profitability"], ["tax-invoice-print", "taxInvPrintZone", "Tax Invoice"]]) {
    w.go(screen); await sleep(700);
    const t = text("#" + zone);
    const produced = screen === "bl-draft";   // the BL / MTD is now produced from the job's data (no placeholder notice)
    const opens = screen === "tax-invoice-print";   // invoices open from the job's Output Docs
    check(`${name}: no sample document, only this job's own data`, !DUMMY.test(t.replace(/Nothing on this page is sample data/, "")) && (opens ? /Open the invoice from its job/.test(t) : produced ? t.includes(job.job_number) && /INMAA/.test(t) : t.includes(job.job_number) && /Real Exports Pvt Ltd/.test(t) && /INMAA/.test(t) && /not been added yet/.test(t)), t.slice(0, 220));
    check(`${name}: no fake action buttons (Release BL, Send Draft ...)`, !/Release BL|Send Draft|Approve/.test(text("#s-" + (screen === "cargo-arrival" ? "cargo-arrival" : screen))));
  }
  w.go("bl-draft"); await sleep(300);
  check("the output-document buttons carry no sample document numbers", !/PASFE|RLPL|J0728|J0191/.test(d.documentElement.innerHTML.replace(/<script[\s\S]*?<\/script>/g, "")));
  // Parties & Cargo drop-downs list the real Organization Master only
  w.go("job-detail", { jobNo: job.job_number }); await sleep(2500);
  const partyOpts = Array.from(d.querySelectorAll("#pc_shipper option")).map(o => o.textContent);
  check("job party drop-downs list the real Organization Master, no sample names", partyOpts.some(x => /Real Exports/.test(x)) && !partyOpts.some(x => DUMMY.test(x)), partyOpts.join("|"));
  // report previews, P&L, meetings, employee profile, rate sheets
  const REPORT = Array.from(d.querySelectorAll("[data-report]"))[0];
  if (REPORT) { w.go("reports"); await sleep(500); REPORT.click(); await sleep(400); check("a report preview shows no invented rows", /No data yet/.test(text("#reportOutputBody")) && !DUMMY.test(text("#reportOutputBody")), text("#reportOutputBody").slice(0, 160)); }
  w.go("pnl"); await sleep(500);
  check("the job profitability screen shows no sample figures", !/62\.4L|49\.8L|12\.6L/.test(text("#s-pnl")) && !DUMMY.test(text("#s-pnl")), text("#s-pnl").slice(0, 200));
  w.go("meetings"); await sleep(400);
  check("meetings shows no sample meeting", /No meetings scheduled yet/.test(text("#s-meetings")) && !DUMMY.test(text("#s-meetings")));
  w.go("emp-detail"); await sleep(400);
  check("the employee profile page holds no sample employee", !/Karthik|₹42,000|eswayfreight/.test(text("#s-emp-detail")));
  w.go("rate-sheet"); await sleep(600);
  check("rate sheets lists no sample sheet", !/Tata Chemicals|RGC-RC-2025/.test(text("#s-rate-sheet")), text("#s-rate-sheet").slice(0, 160));
  // a button that is not built yet no longer pretends
  d.querySelectorAll("#toastStack .toast").forEach(t => t.remove());
  const fake = d.getElementById("invEmailBtn"); if (fake) { fake.click(); await sleep(300); check("an unbuilt action says it is not available instead of claiming success", Array.from(d.querySelectorAll("#toastStack .toast")).some(t => /Not available yet/.test(t.textContent) && t.classList.contains("warn")) && !Array.from(d.querySelectorAll("#toastStack .toast")).some(t => /emailed|Sent to/i.test(t.textContent))); }

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
