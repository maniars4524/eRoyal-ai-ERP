// Branch master: Organisation is the signed-in company, save / reopen, and the Audit Log tab.
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
  const org = await j("POST", "/api/organizations", { code: "BR" + tag.slice(-6).toUpperCase(), name: "BranchOrg " + tag, admin_email: `br${tag}@t.test`, admin_username: "br" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "br" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);

  const S = () => d.getElementById("s-branch-detail");
  const field = lab => Array.from(S().querySelectorAll(".fg")).find(g => { const l = g.querySelector("label"); return l && l.textContent.trim().toLowerCase().startsWith(lab.toLowerCase()); });
  const setv = (g, v) => { const e = g.querySelector("input,select"); e.value = v; e.dispatchEvent(new w.Event("input", { bubbles: true })); e.dispatchEvent(new w.Event("change", { bubbles: true })); };
  const companyName = org.body.organization.name;
  w.go("branch-detail"); await sleep(2500);
  const orgSel = field("Organisation").querySelector("select");
  check("the Organisation of a new branch is the signed-in company, not a sample name", orgSel.options.length === 1 && orgSel.value === companyName && !/eSway ERP 2\.0 Pvt Ltd/.test(S().innerHTML), orgSel.value + " | " + companyName);
  check("the branch screen shows no sample branch name or code", !/Chennai HQ|CHN-001/.test(S().querySelector(".ph").textContent), S().querySelector(".ph").textContent.slice(0, 120));
  setv(field("Branch Code"), "MUM1"); setv(field("Branch Name"), "MUMBAI"); setv(d.getElementById("br_city").closest(".fg"), "Mumbai");
  Array.from(S().querySelectorAll("button")).find(b => /Save Branch/.test(b.textContent)).click(); await sleep(2500);
  const list = (await j("GET", "/api/masters/branch", null, token)).body;
  check("the branch is saved", list.length === 1 && list[0].code === "MUM1", JSON.stringify(list.map(x => x.code)));
  const rec = list[0];
  w.MK.pending = { type: "branch", id: rec.id, rec }; w.go("branch-detail"); await sleep(2500);
  check("reopening shows the saved values and the company name", field("Branch Name").querySelector("input").value === "MUMBAI" && field("Organisation").querySelector("select").value === companyName);
  setv(d.getElementById("br_city").closest(".fg"), "Navi Mumbai"); setv(field("Branch Name"), "MUMBAI HUB");
  Array.from(S().querySelectorAll("button")).find(b => /Save Branch/.test(b.textContent)).click(); await sleep(2500);
  w.MK.pending = { type: "branch", id: rec.id, rec: (await j("GET", "/api/masters/branch/" + rec.id, null, token)).body }; w.go("branch-detail"); await sleep(2500);
  w.document.querySelector('#s-branch-detail [data-tab="br11"]').click(); await sleep(800);
  const rows = Array.from(d.querySelectorAll("#br11 tbody tr")).map(tr => tr.textContent.replace(/\s+/g, " ").trim());
  check("the Audit Log tab lists who changed which field from what to what", rows.some(r => /Updated/.test(r) && /Branch Name|Name/.test(r) && /MUMBAI HUB/.test(r)) && rows.some(r => /Created/.test(r)) && rows.some(r => /Navi Mumbai/.test(r)), rows.join(" || ").slice(0, 400));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
