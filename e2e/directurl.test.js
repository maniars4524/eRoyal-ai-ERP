// Security check: opening the address directly always shows the login screen; a reload in the same tab keeps the session.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json().catch(() => null) };
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "DU" + tag.slice(-6).toUpperCase(), name: "Direct " + tag, admin_email: `du${tag}@t.test`, admin_username: "du" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "D" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "du" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;

  const open = async navType => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) {
        w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.scrollTo = () => {}; w.console.error = () => {};
        w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token);
        w.performance.getEntriesByType = t => (t === "navigation" ? [{ type: navType }] : []);
      } });
    await sleep(6500);
    return dom.window;
  };
  const visible = w => { const o = w.document.getElementById("loginOverlay"); return !!o && o.style.display !== "none" && !o.classList.contains("hidden") && !w.document.getElementById("loginOverlayPreHide"); };

  let w = await open("navigate");
  check("typing the address shows the login screen even with a saved session", visible(w));
  check("the saved tokens are discarded", w.sessionStorage.getItem("esway_access_token") === null && w.localStorage.getItem("esway_persistent_token") === null);
  w.close();

  w = await open("reload");
  check("a reload in the same tab keeps the signed-in session", !visible(w) && w.sessionStorage.getItem("esway_access_token") === token);
  w.close();

  // ---------- Remember me: username, license number and password are pre-filled (never auto-signed-in)
  const lic = org.body.organization.license_number;
  const fresh = async stored => {
    const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(x) { x.fetch = (u, o) => fetch(new URL(u, BASE).href, o); x.scrollTo = () => {}; x.console.error = () => {}; if (stored) x.localStorage.setItem("esway_remember_erp", stored); } });
    await sleep(2200); return dom.window;
  };
  const type = (x, id, v) => { const el = x.document.getElementById(id); el.value = v; el.dispatchEvent(new x.Event("input", { bubbles: true })); };
  let x = await fresh(null);
  type(x, "loginLicense", lic); type(x, "loginUser", "du" + tag); type(x, "loginPass", "Ui-Test-Passw0rd-1"); x.document.getElementById("loginRemember").checked = true;
  x.document.getElementById("loginBtn").click(); await sleep(2500);
  const saved = x.localStorage.getItem("esway_remember_erp");
  check("ticking Remember me stores the sign-in details for this browser", !!saved && Object.keys(JSON.parse(saved)).join() === "u,l,p", saved);
  x.close();
  x = await fresh(saved);
  check("next visit: username, license number and password are pre-filled and the box is ticked", x.document.getElementById("loginUser").value === "du" + tag && x.document.getElementById("loginLicense").value === lic && x.document.getElementById("loginPass").value === "Ui-Test-Passw0rd-1" && x.document.getElementById("loginRemember").checked);
  check("pre-filled details do not sign in by themselves", !x.sessionStorage.getItem("esway_access_token"));
  x.document.getElementById("loginRemember").checked = false; x.document.getElementById("loginBtn").click(); await sleep(2500);
  check("signing in with Remember me unticked erases the saved details", x.localStorage.getItem("esway_remember_erp") === null);
  x.close();

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
