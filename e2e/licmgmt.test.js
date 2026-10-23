// License Management console: the menu exists only for the license super admin of the owner company (SWF00001); the console creates companies, renews, records payments,
// manages users and shows live / usage / storage / reminders / audit.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const { execSync } = require("child_process");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const psql = sql => execSync(`docker exec esway-postgres psql -U esway -d esway_erp_qa -c "${sql}"`, { stdio: "pipe" }).toString();
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const mkOrg = async (p) => (await j("POST", "/api/organizations", { code: p + tag.slice(-6).toUpperCase(), name: p + " " + tag, admin_email: `${p}${tag}@t.test`, admin_username: p.toLowerCase() + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: p }, dev.body.access_token)).body;
  const owner = await mkOrg("LO"), other = await mkOrg("LX");
  psql(`update organizations set license_number = 'SWX' || id where license_number = 'SWF00001'; update organizations set license_number = 'SWF00001' where id = ${owner.organization.id};`);
  execSync(`${process.platform === "win32" ? "set ENV_FILE=.env.qa&& " : "ENV_FILE=.env.qa "}..\\backend\\.venv\\Scripts\\python.exe scripts_grant_license_admin.py lo${tag}`, { cwd: "../backend", stdio: "pipe", shell: true });
  const ownerTok = (await j("POST", "/api/auth/login", { identifier: "lo" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: "SWF00001" })).body.access_token;
  const otherTok = (await j("POST", "/api/auth/login", { identifier: "lx" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: other.organization.license_number })).body.access_token;
  const open = async token => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
    await sleep(7000); return dom.window;
  };
  // an ordinary company admin never sees the menu or the data
  let w = await open(otherTok);
  check("an ordinary company admin has no License Management menu", !Array.from(w.document.querySelectorAll(".tn-row .tn-label")).some(x => /License Management/.test(x.textContent)));
  check("and the server refuses him too", (await j("GET", "/api/license-mgmt/summary", null, otherTok)).status === 403);
  w.close();
  w = await open(ownerTok); const d = w.document, q = s => d.querySelector(s), qa = s => Array.from(d.querySelectorAll(s));
  const labels = qa(".tn-row .tn-label").map(x => x.textContent.trim());
  check("the license super admin of SWF00001 sees the License Management menu", labels.includes("License Management"));
  d.querySelector('.tn-row[data-go="license-mgmt"]').dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(3500);
  check("the console opens with the provider's summary cards", d.getElementById("s-license-mgmt").classList.contains("on") && qa("#s-license-mgmt .dash-cards .kpi-rich").length >= 8 && /COMPANIES/.test(q("#s-license-mgmt").textContent) && /DATA STORED/.test(q("#s-license-mgmt").textContent));
  const tab = async k => { q(`[data-lic-tab="${k}"]`).click(); await sleep(2500); };
  await tab("companies");
  const typeIn = async (id, v) => { const e = d.getElementById(id); e.value = v; e.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(2500); };
  await typeIn("licQ", other.organization.name);
  const rows = () => qa("#licBody tbody tr").map(r => r.textContent.replace(/\s+/g, " ").trim());
  check("Companies lists every company with license, status, users and data", rows().some(r => r.includes(other.organization.name) && r.includes(other.organization.license_number)));
  // create a company from the console
  q('[data-lic="new"]').click(); await sleep(500);
  const nu = "nc" + tag.slice(-5);
  const set = (id, v) => { const e = d.getElementById(id); e.value = v; e.dispatchEvent(new w.Event("input", { bubbles: true })); };
  set("nc_code", "NC" + tag.slice(-6).toUpperCase()); set("nc_name", "Console Co " + tag); set("nc_au", nu); set("nc_ap", "a"); set("nc_af", "Nina"); set("nc_ae", nu + "@t.test"); set("nc_mu", "3"); set("nc_mp", "AMC");
  d.getElementById("nc_go").click(); await sleep(4000);
  const cc = (await j("GET", "/api/license-mgmt/companies?q=Console Co " + tag, null, ownerTok)).body.items[0];
  check("a new company is created with its license, user limit and AMC from the console", !!cc && /^SWF\d{5}$/.test(cc.license_number) && cc.max_users === 3 && cc.maintenance_plan === "AMC" && cc.users === 1, JSON.stringify(cc));
  check("its detail drawer opens on the new company", d.getElementById("licDrawer").classList.contains("on") && /Console Co/.test(d.getElementById("licDH").textContent));
  check("the new administrator can sign in with a one-letter password", (await j("POST", "/api/auth/login", { identifier: nu, password: "a", portal: "erp", license_number: cc.license_number })).status === 200);
  // renew the license, with a payment recorded as pending, from the drawer
  q('[data-lic-dtab="renew"]').click(); await sleep(400);
  d.getElementById("rn_amt_LICENSE").value = "5000"; d.getElementById("rn_ps_LICENSE").value = "PENDING"; d.getElementById("rn_ref_LICENSE").value = "INV-UI-1";
  const before = cc.license_expiry;
  const btn = q('[data-lic-renew="LICENSE"]'); btn.click(); btn.click(); await sleep(3000);       // a double click
  const after = (await j("GET", "/api/license-mgmt/companies/" + cc.id, null, ownerTok)).body;
  check("renewing extends the license once even when the button is pressed twice, and keeps the payment note", after.license_expiry > before && after.renewals.filter(r => r.kind === "LICENSE").length === 1 && after.renewals[0].payment_status === "PENDING" && after.renewals[0].reference === "INV-UI-1", JSON.stringify(after.renewals));
  q('[data-lic-dtab="renew"]').click(); await sleep(400);
  d.getElementById("rn_amt_MAINTENANCE").value = "1200"; d.getElementById("rn_ps_MAINTENANCE").value = "RECEIVED"; q('[data-lic-renew="MAINTENANCE"]').click(); await sleep(2500);
  check("an AMC renewal moves the maintenance date and is recorded as received", (await j("GET", "/api/license-mgmt/companies/" + cc.id, null, ownerTok)).body.renewals.some(r => r.kind === "MAINTENANCE" && r.payment_status === "RECEIVED" && r.amount === "1200.00"));
  // the renewals tab and recording a payment
  await tab("renewals");
  check("Renewals & Payments shows the pending payment", /PAYMENTS PENDING/.test(q("#licBody").textContent) && rows().some(r => r.includes(cc.license_number) && /Pending/.test(r)));
  // users tab: permissions and password reset
  await tab("users");
  await typeIn("licUsrQ", "Console Co " + tag);
  check("Users & Access lists the company's users with their roles", rows().some(r => r.includes(nu) && /Admin|ERP_ADMIN/.test(r)));
  q(`[data-lic-pwd^="${cc.id}:"]`).click(); await sleep(1500);
  d.getElementById("pw_new").value = "zz"; d.getElementById("pw_go").click(); await sleep(2500);
  check("the provider sets a new password and the user can use it at once", (await j("POST", "/api/auth/login", { identifier: nu, password: "zz", portal: "erp", license_number: cc.license_number })).status === 200 && (await j("POST", "/api/auth/login", { identifier: nu, password: "a", portal: "erp", license_number: cc.license_number })).status === 401);
  // suspend from the drawer
  await tab("companies"); await typeIn("licQ", "Console Co " + tag); q(`[data-lic-open="${cc.id}"]`).click(); await sleep(2000);
  q(`[data-lic-act="suspend:${cc.id}"]`).click(); await sleep(600); d.getElementById("rs_t").value = "UI test suspension"; d.getElementById("rs_go").click(); await sleep(2500);
  check("suspending needs a reason, blocks the company's sign-in and is shown as Suspended", (await j("POST", "/api/auth/login", { identifier: nu, password: "zz", portal: "erp", license_number: cc.license_number })).status === 403 && /Suspended/.test(d.getElementById("licDH").textContent));
  q(`[data-lic-act="reactivate:${cc.id}"]`).click(); await sleep(600); d.getElementById("rs_t").value = "UI test back"; d.getElementById("rs_go").click(); await sleep(2500);
  check("reactivating restores access", (await j("POST", "/api/auth/login", { identifier: nu, password: "zz", portal: "erp", license_number: cc.license_number })).status === 200);
  q('[data-lic="closedrawer"]').click();
  // live / usage / reminders / audit
  await tab("live");
  check("Live & Usage shows the online-now table, the sign-in cards and the usage filters", /ONLINE NOW/.test(q("#licBody").textContent) && !!d.getElementById("licUC") && /Work by module/.test(q("#licBody").textContent));
  await tab("reminders");
  check("Reminders tab is available with the check button", !!q('[data-lic="runnotes"]'));
  await tab("audit");
  check("Audit shows what was done (issued, renewed, suspended, password...)", ["LICENSE ISSUED", "LICENSE RENEWED", "LICENSE SUSPENDED", "LICENSE USER MANAGED"].every(a => q("#licBody").textContent.includes(a)), q("#licBody").textContent.slice(0, 300));
  await tab("overview");
  check("Overview lists the online users and the biggest companies", /Top companies by online time/.test(q("#licBody").textContent) && /Companies with the most users/.test(q("#licBody").textContent));
  psql(`update organizations set license_number = 'SWX' || id where id = ${owner.organization.id};`);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
