// Login check for the three portals against a running server (default: dev server on :8000).
// Reads credentials from ../.env (developer, demo) and LOGIN_* env vars for the ERP admin; never prints passwords.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = process.env.BASE || "http://127.0.0.1:8000";
const env = Object.fromEntries(fs.readFileSync("../.env", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const ACCOUNTS = {
  developer: { user: env.SUPER_ADMIN_USERNAME, pass: env.SUPER_ADMIN_PASSWORD, license: undefined, path: "/developer/login" },
  demo: { user: env.DEMO_PORTAL_USERNAME, pass: env.DEMO_PORTAL_PASSWORD, license: undefined, path: "/demo/login" },
  erp: { user: process.env.LOGIN_ERP_USER || "admin", pass: process.env.LOGIN_ERP_PASS || "admin", license: process.env.LOGIN_ERP_LICENSE || "RTS0004", path: "/erp/login" },
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const login = async (identifier, password, portal, license) => {
  const r = await fetch(BASE + "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identifier, password, portal, license_number: license }) });
  return { status: r.status, body: await r.json().catch(() => ({})) };
};

(async () => {
  console.log("== API checks ==");
  for (const [portal, a] of Object.entries(ACCOUNTS)) {
    const ok = await login(a.user, a.pass, portal, a.license);
    check(`${portal}: valid login succeeds on its own portal`, ok.status === 200 && ok.body.user && ok.body.user.portal === portal, ok.status + " " + JSON.stringify(ok.body).slice(0, 120));
    if (ok.status === 200) {
      const me = await fetch(BASE + "/api/auth/me", { headers: { Authorization: "Bearer " + ok.body.access_token } });
      check(`${portal}: session is valid (/api/auth/me)`, me.status === 200 && (await me.json()).portal === portal);
      const out = await fetch(BASE + "/api/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + ok.body.access_token } });
      check(`${portal}: logout works`, out.status === 200);
    }
    const bad = await login(a.user, a.pass + "x", portal, a.license);
    check(`${portal}: wrong password is rejected (401, generic)`, bad.status === 401 && bad.body.detail === "Invalid credentials", bad.status + " " + JSON.stringify(bad.body));
    for (const other of Object.keys(ACCOUNTS).filter(p => p !== portal)) {
      const wrong = await login(a.user, a.pass, other, a.license);
      check(`${portal} account cannot use the ${other} portal (403)`, wrong.status === 403, wrong.status + " " + JSON.stringify(wrong.body).slice(0, 100));
    }
    // a successful login resets the failure counter, so repeated test runs never lock the account
    await login(a.user, a.pass, portal, a.license);
  }
  const noLic = await login(ACCOUNTS.erp.user, ACCOUNTS.erp.pass, "erp", undefined);
  check("erp: license number is required", noLic.status === 401);
  const badLic = await login(ACCOUNTS.erp.user, ACCOUNTS.erp.pass, "erp", "RTS9999");
  check("erp: wrong license number is rejected", badLic.status === 401);
  await login(ACCOUNTS.erp.user, ACCOUNTS.erp.pass, "erp", ACCOUNTS.erp.license);
  const unknown = await login("nobody-here", "whatever-123456", "erp", "RTS0001");
  check("unknown user gets the same generic 401", unknown.status === 401 && unknown.body.detail === "Invalid credentials");
  const anon = await fetch(BASE + "/api/customers");
  check("protected API without a token is 401", anon.status === 401);
  const tamper = await fetch(BASE + "/api/auth/me", { headers: { Authorization: "Bearer " + (await login(ACCOUNTS.erp.user, ACCOUNTS.erp.pass, "erp", ACCOUNTS.erp.license)).body.access_token.slice(0, -3) + "abc" } });
  check("tampered token is rejected", tamper.status === 401);

  console.log("== Login page checks (real page, real API) ==");
  for (const [portal, a] of Object.entries(ACCOUNTS)) {
    const dom = await JSDOM.fromURL(BASE + a.path, {
      runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) {
        w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
        w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {};
      },
    });
    const w = dom.window, d = w.document;
    await sleep(2000);
    const licenseGroup = d.getElementById("loginLicense").closest(".login-field");
    check(`${portal}: page title and license field match the portal`, (portal === "erp" ? licenseGroup.style.display !== "none" : licenseGroup.style.display === "none") && new RegExp(portal === "developer" ? "Administration" : portal === "demo" ? "Demo" : "^ERP$").test(d.querySelector(".login-title").textContent.trim()), d.querySelector(".login-title").textContent);
    // wrong password shows an error and keeps the form
    d.getElementById("loginLicense").value = a.license || ""; d.getElementById("loginUser").value = a.user; d.getElementById("loginPass").value = a.pass + "x";
    d.getElementById("loginBtn").click(); await sleep(2200);
    const err = d.getElementById("loginErr");
    check(`${portal}: wrong password shows an error on the page`, err.classList.contains("show") && err.textContent.length > 5 && !d.getElementById("loginOverlay").classList.contains("hidden"), err.textContent);
    // correct login signs in
    d.getElementById("loginPass").value = a.pass;
    d.getElementById("loginBtn").click(); await sleep(2500);
    check(`${portal}: correct login hides the login screen and stores a session`, d.getElementById("loginOverlay").classList.contains("hidden") && !!w.sessionStorage.getItem("esway_access_token"), "overlay hidden=" + d.getElementById("loginOverlay").classList.contains("hidden"));
    const name = (d.querySelector(".hdr-uname") || {}).textContent || "";
    check(`${portal}: header shows the signed-in user and role`, name.trim().length > 0 && ((d.getElementById("userRoleLbl") || {}).textContent || "").length > 0, name + " / " + (d.getElementById("userRoleLbl") || {}).textContent);
    // sign out
    const signOut = Array.from(d.querySelectorAll(".hdr-menu-item")).find(x => /sign out/i.test(x.textContent));
    signOut.click(); await sleep(800);
    check(`${portal}: sign out clears the session and shows the login again`, !w.sessionStorage.getItem("esway_access_token") && d.getElementById("loginOverlay").style.display === "flex", "token=" + !!w.sessionStorage.getItem("esway_access_token"));
    w.close();
  }
  console.log("== Remember-me session on reload ==");
  const devTok = (await login(ACCOUNTS.developer.user, ACCOUNTS.developer.pass, "developer")).body.access_token;
  const open = async (path, token) => {
    const dom = await JSDOM.fromURL(BASE + path, { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; if (token) w.localStorage.setItem("esway_persistent_token", token); } });
    await sleep(4000); return dom.window;
  };
  let w2 = await open("/developer/login", devTok);
  const hidden = w => w.document.getElementById("loginOverlay").classList.contains("hidden") || w.document.getElementById("loginOverlay").style.display === "none";
  check("developer session restores on reload without asking for a password", hidden(w2), "overlay display=" + w2.document.getElementById("loginOverlay").style.display); w2.close();
  w2 = await open("/erp/login", devTok);
  check("a developer session is NOT accepted on the ERP portal (login shown with a message)", !hidden(w2) && /different portal/i.test(w2.document.getElementById("loginErr").textContent), "hidden=" + hidden(w2) + " msg=" + w2.document.getElementById("loginErr").textContent); w2.close();
  w2 = await open("/erp/login", "not-a-real-token");
  check("an invalid stored token falls back to the login screen", !hidden(w2)); w2.close();

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL LOGIN CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
