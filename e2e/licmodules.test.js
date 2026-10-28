// Company licence modules: a CHA + Accounts company has no Masters / CRM / Operations, Logistics & Customs shows only CHA, invoices offer only the CHA category, the dashboard only the CHA slide.
// CHA screen: Back + Create Job (Export / Import x Sea / Air), dashboard cards (Draft, Pending, Filed, Delayed, Completed ...), register, job window with stages.
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
  const org = await j("POST", "/api/organizations", { code: "LM" + tag.slice(-6).toUpperCase(), name: "LicMod " + tag, admin_email: `lm${tag}@t.test`, admin_username: "lm" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "lm" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const { execSync } = require("child_process");
  const py = ["import os,sys", 'os.environ["ENV_FILE"]=".env.qa"', 'sys.path.insert(0,"E:/eRoyal-ai-ERP/backend")', 'os.chdir("E:/eRoyal-ai-ERP/backend")', "from app.db import SessionLocal", "from app.models import Organization", "d=SessionLocal(); o=d.get(Organization," + org.body.organization.id + "); o.modules=['customs','accounts']; d.commit()"].join(String.fromCharCode(10));
  require("fs").writeFileSync(process.env.TEMP + "/setmods.py", py); execSync(`"E:/eRoyal-ai-ERP/backend/.venv/Scripts/python.exe" -I "${process.env.TEMP}/setmods.py"`);
  const dom2 = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; } });
  const w2 = dom2.window, d2 = w2.document; await sleep(7000);
  const tops = Array.from(d2.querySelectorAll("#topnavMenu > .tn-item > .tn-row .tn-label")).map(x => x.textContent.trim());
  check("a CHA + Accounts licence shows Accounts and Logistics & Customs, but no Masters, CRM or Operations", tops.includes("Accounts") && tops.includes("Logistics & Customs") && !tops.includes("Masters") && !tops.includes("CRM") && !tops.includes("Operations"), tops.join());
  const subs = Array.from(d2.querySelectorAll(".tn-panel .tn-label")).map(x => x.textContent.trim());
  check("Logistics & Customs has only CHA (no Booking, Transport or Warehouse)", subs.includes("CHA") && !subs.includes("Transport") && !subs.includes("Warehouse") && !subs.includes("Booking"), subs.join("|"));
  d2.querySelector(".hdr-menu-item[data-role=\"Admin\"]").click(); await sleep(300);
  w2.go("invoice-new"); await sleep(2500);
  const cats = Array.from(d2.querySelectorAll("#s-invoice-new [data-bk-svc]")).map(x => x.dataset.bkSvc);
  check("the invoice offers only the CHA category", cats.join() === "CHA", cats.join());
  w2.go("dashboard"); await sleep(1500);
  const dots = Array.from(d2.querySelectorAll(".dash-dot")).filter(x => x.style.display !== "none").map(x => x.title);
  check("the dashboard keeps only the CHA slide among the business lines", dots.includes("CHA (Customs)") && !dots.includes("Freight") && !dots.includes("Transport"), dots.join());
  const dn = await fetch(BASE + "/api/cha/dashboard", { headers: { Authorization: "Bearer " + token } });
  check("the CHA API still works for this licence", dn.status === 200);
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
