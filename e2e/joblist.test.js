// Headless UI check: the Jobs / Shipments list keeps job numbers and party names on one line, can freeze the Job No and the first party column, and leads with the consignee on import jobs.
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
  const org = await j("POST", "/api/organizations", { code: "JL" + tag.slice(-5).toUpperCase(), name: "Joblist " + tag, admin_email: `jl${tag}@t.test`, admin_username: "jl" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "J" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jl" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const exp = (await j("POST", "/api/customers", { code: "JLE", name: "Export Shipper Co", roles: ["shipper"], data: {} }, token)).body;
  const imp = (await j("POST", "/api/customers", { code: "JLI", name: "Import Consignee Co", roles: ["consignee"], data: {} }, token)).body;
  const e = await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "SGSIN", customer_id: exp.id, consignee: "Overseas Buyer LLC" }, token);
  const i = await j("POST", "/api/shipments", { mode: "Sea", direction: "Import", container_mode: "FCL", pol: "SGSIN", pod: "INMAA", customer_id: imp.id, consignee: "Import Consignee Co" }, token);
  check("setup: an export and an import job", e.status === 201 && i.status === 201, JSON.stringify([e.body, i.body]).slice(0, 200));
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  await sleep(3500); w.go("job-list"); await sleep(3000);
  const rowOf = no => qa("#jobTableBody tr[data-job-row]").find(r => r.textContent.includes(no));
  const heads = () => qa("#jobTable thead tr:first-child th").slice(0, 3).map(t => t.textContent.trim());
  const er = rowOf(e.body.job_number), ir = rowOf(i.body.job_number);
  check("all directions: the party columns read Shipper / Consignee and Consignee / Shipper", heads().join("|") === "Job No|Shipper / Consignee|Consignee / Shipper", heads().join("|"));
  check("an export row leads with the shipper, then the consignee", er.children[1].textContent.trim() === "Export Shipper Co" && er.children[2].textContent.trim() === "Overseas Buyer LLC", er.children[1].textContent + " | " + er.children[2].textContent);
  check("an import row leads with the consignee, then the shipper", ir.children[1].textContent.trim() === "Import Consignee Co" && ir.children[2].textContent.trim() === "Import Consignee Co".replace("Import Consignee Co", ir.children[2].textContent.trim()) && ir.dataset.partySwapped === "1", ir.children[1].textContent + " | " + ir.children[2].textContent);
  const f = q("#jobDirFilter"); f.value = "Import"; f.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(500);
  check("import filter: headers read Consignee then Shipper", heads().join("|") === "Job No|Consignee|Shipper", heads().join("|"));
  f.value = "Export"; f.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(500);
  check("export filter: headers read Shipper then Consignee", heads().join("|") === "Job No|Shipper|Consignee", heads().join("|"));
  f.value = ""; f.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  const css = qa("style").map(s => s.textContent).join("\n");
  check("job number and names are set to stay on one line", /#jobTable th,#jobTable td\{white-space:nowrap\}/.test(css));
  check("Job No and the first party column are frozen by default", q("#s-job-list").classList.contains("jt-freeze") && q("#jobFreezeToggle").checked && /jt-freeze #jobTable thead th:nth-child\(2\)[^{]*\{left:165px/.test(css));
  q("#jobFreezeToggle").checked = false; q("#jobFreezeToggle").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("the Freeze option can be switched off (and is remembered)", !q("#s-job-list").classList.contains("jt-freeze") && w.localStorage.getItem("esway_job_freeze") === "0");
  q("#jobFreezeToggle").checked = true; q("#jobFreezeToggle").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200);
  check("and back on", q("#s-job-list").classList.contains("jt-freeze") && w.localStorage.getItem("esway_job_freeze") === "1");
  check("the table keeps its sideways scrolling and every column", q("#jobTable").closest(".tw") !== null && qa("#jobTable thead tr:first-child th").length >= 10);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
