// New Job: no sample consignee / carrier names; the carrier list follows the mode (Air = Airline Master, Sea = Sea Line Master, never mixed); the consignee list is the company's own organizations.
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
  const org = await j("POST", "/api/organizations", { code: "CR" + tag.slice(-6).toUpperCase(), name: "Carriers " + tag, admin_email: `cr${tag}@t.test`, admin_username: "cr" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Cara" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cr" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const air = "Skyline " + tag, sea = "Oceanic " + tag, cons = "Importer " + tag;
  check("an airline is added to the master", (await j("POST", "/api/network/airlines", { code: "A" + tag.slice(-2).toUpperCase(), name: air }, token)).status === 201);
  check("a sea line is added to the master", (await j("POST", "/api/network/sea-lines", { code: "S" + tag.slice(-3).toUpperCase(), name: sea }, token)).status === 201);
  check("a consignee is added to the Organization master", (await j("POST", "/api/customers", { code: "IM" + tag.slice(-5).toUpperCase(), name: cons, roles: ["consignee"], data: {} }, token)).status === 201);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  w.go("job-new"); await sleep(2500);
  const opts = id => Array.from(d.getElementById(id).options).map(o => o.textContent.trim());
  const setMode = async m => { const s = d.getElementById("nj_mode"); s.value = m; s.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(1500); };
  const dummy = /MSC Mediterranean|Hapag|ONE Line|Air India|Emirates|Solvay|Honda|CMA CGM|Singapore Airlines/;
  check("the consignee list has no sample names, only the company's own consignee", !opts("nj_consignee").some(x => dummy.test(x)) && opts("nj_consignee").some(x => x.startsWith(cons)), opts("nj_consignee").join("|"));
  await setMode("Sea"); d.getElementById("nj_dir").value = "Import"; d.getElementById("nj_dir").dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(800);
  check("Sea Import shows only sea lines from the master (no sample carriers, no airline)", opts("nj_carrier").includes(sea) && !opts("nj_carrier").includes(air) && !opts("nj_carrier").some(x => dummy.test(x)), opts("nj_carrier").join("|"));
  await setMode("Air");
  check("Air shows only airlines from the master (no sea line, no sample names)", opts("nj_carrier").includes(air) && !opts("nj_carrier").includes(sea) && !opts("nj_carrier").some(x => dummy.test(x)), opts("nj_carrier").join("|"));
  await setMode("Road");
  check("Road has no carrier list", opts("nj_carrier").length === 1, opts("nj_carrier").join("|"));
  check("the booking form has no sample carriers either", !Array.from(d.querySelectorAll("#bk_carrier option")).some(o => dummy.test(o.textContent)));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
