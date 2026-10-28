// Organization roles (Exporter / Importer / CHA) open their own master options; the CHA screen picks parties and defaults from them.
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
  const org = await j("POST", "/api/organizations", { code: "FR" + tag.slice(-6).toUpperCase(), name: "FormRd " + tag, admin_email: `fr${tag}@t.test`, admin_username: "fr" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fr" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const cust = async (code, name, roles, data) => (await j("POST", "/api/customers", { code, name, roles, data, country: "India" }, token));
  const click = async sel => { d.querySelector(sel).click(); await sleep(500); };
  w.go("org-new"); await sleep(1500);

  const iec = d.getElementById("org_iec"), tan = d.getElementById("org_tan"), st = d.getElementById("org_state");
  await sleep(1800);
  check("an empty IEC field shows 'Enter IEC Code (e.g., ...)' as its placeholder", iec.getAttribute("placeholder") === "Enter IEC Code (e.g., 0512098765)", iec.getAttribute("placeholder"));
  check("TAN gets the same helpful placeholder", tan.getAttribute("placeholder") === "Enter TAN (e.g., BLRG19369A)", tan.getAttribute("placeholder"));
  check("the placeholder is not the value", iec.value === "" && tan.value === "");
  check("a drop-down still on its empty choice is marked as a placeholder (grey)", st.classList.contains("ff-empty"));
  st.selectedIndex = 3; st.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("choosing a state clears the grey marker", !st.classList.contains("ff-empty"));
  const css = d.getElementById("formReadability").textContent;
  check("the global colours are #9CA3AF placeholder, #334155 value and #475569 label", css.includes("--ff-ph:#9CA3AF") && css.includes("--ff-val:#334155") && css.includes("--ff-lbl:#475569"));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
