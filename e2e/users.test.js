// Headless UI check #3: User Master (real accounts, roles, password reset) against the QA database.
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
  const org = await j("POST", "/api/organizations", { code: "US" + tag.slice(-6).toUpperCase(), name: "Users " + tag, admin_email: `us${tag}@t.test`, admin_username: "us" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Admin", admin_last_name: "One" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const login = await j("POST", "/api/auth/login", { identifier: "us" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic });
  const token = login.body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const toasts = []; const orig = w.toast; w.toast = (a, b, c) => { toasts.push(a + " | " + b); try { orig && orig(a, b, c); } catch (e) {} };
  const field = (label) => { const fg = Array.from(d.querySelectorAll("#s-user-detail .fg")).find(x => { const l = x.querySelector("label.fl"); return l && l.textContent.replace(/\*/g, "").trim().toLowerCase().startsWith(label.toLowerCase()); }); return fg && fg.querySelector("input,select,textarea"); };
  const rows = () => Array.from(d.querySelectorAll("#s-user-master tbody tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());
  const saveBtn = () => Array.from(d.querySelectorAll("#s-user-detail .pa button")).find(b => /save/i.test(b.textContent));

  await j("POST", "/api/masters/designation", { code: "ACC", name: "Accounts Exec" }, token);
  await j("POST", "/api/masters/department", { code: "FIN", name: "Finance" }, token);
  w.go("user-master"); await sleep(800);
  check("user list shows only the real admin (no dummy users)", rows().length === 1 && rows()[0].includes("us" + tag) && !/Rajan|Suresh|Mani/.test(rows().join("")), rows().join("|"));

  w.go("user-detail"); await sleep(800);
  check("new user form is blank with a password field and role dropdown from the server", field("Username").value === "" && !!d.getElementById("uk_pwd") && Array.from(field("Primary Role").options).map(o => o.text).join(",") === "Admin,Operations,Accounts,Viewer", Array.from(field("Primary Role").options).map(o => o.text).join(","));
  saveBtn().click(); await sleep(300);
  check("designation/department dropdowns come from their masters", Array.from(field("Designation").options).map(o=>o.text).join(",") === "— Select —,Accounts Exec" && Array.from(field("Department").options).map(o=>o.text).join(",") === "— Select —,Finance");
  check("empty user form is blocked", toasts.some(t => /Required fields missing/.test(t)));
  field("First Name").value = "Priya"; field("Last Name").value = "Nair"; field("Designation").value = "Accounts Exec"; field("Username").value = "priya" + tag;
  field("Work Email").value = `priya${tag}@t.test`; field("Mobile").value = "9000000009"; field("Primary Role").value = "ACCOUNTS";
  d.getElementById("uk_pwd").value = "";
  toasts.length = 0; saveBtn().click(); await sleep(300);
  check("an empty password is refused (any other length is the user's choice)", toasts.some(t => /Enter a password/.test(t)));
  d.getElementById("uk_pwd").value = "Priya-Passw0rd-123";
  saveBtn().click(); await sleep(1500);
  const list = (await j("GET", "/api/users", null, token)).body;
  const priya = list.find(u => u.username === "priya" + tag);
  check("user persisted with role and profile", !!priya && priya.roles[0] === "ACCOUNTS" && priya.profile.designation === "Accounts Exec" && priya.phone === "9000000009", JSON.stringify(priya) + toasts.join(";"));
  check("new user can sign in with the password set in the form", (await j("POST", "/api/auth/login", { identifier: "priya" + tag, password: "Priya-Passw0rd-123", portal: "erp", license_number: lic })).status === 200);
  check("list shows the new user with role", rows().some(t => t.includes("priya" + tag) && t.includes("Accounts")), rows().join("|"));

  // edit flow + deactivate
  d.querySelector('[data-uk-edit="' + priya.id + '"]').click(); await sleep(900);
  check("edit form is loaded from the database", field("First Name").value === "Priya" && field("Username").disabled === true && field("Primary Role").value === "ACCOUNTS" && d.getElementById("uk_pwd_fg").style.display === "none");
  field("Primary Role").value = "VIEWER"; field("Status").value = "Inactive";
  saveBtn().click(); await sleep(1300);
  const after = (await j("GET", "/api/users/" + priya.id, null, token)).body;
  check("edit persisted (role and status)", after.roles[0] === "VIEWER" && after.status === "INACTIVE", JSON.stringify(after));
  check("deactivated user can no longer sign in", (await j("POST", "/api/auth/login", { identifier: "priya" + tag, password: "Priya-Passw0rd-123", portal: "erp", license_number: lic })).status === 401);

  // reset password via modal
  d.querySelector('[data-uk-edit="' + priya.id + '"]').click(); await sleep(800);
  field("Status").value = "Active"; saveBtn().click(); await sleep(1000);
  d.querySelector('[data-uk-edit="' + priya.id + '"]').click(); await sleep(800);
  Array.from(d.querySelectorAll("#s-user-detail .pa button")).find(b => /reset password/i.test(b.textContent)).click(); await sleep(400);
  d.getElementById("uk_newpwd").value = "Reset-Passw0rd-9999"; d.getElementById("uk_resetgo").click(); await sleep(1200);
  check("password reset from the UI works", (await j("POST", "/api/auth/login", { identifier: "priya" + tag, password: "Reset-Passw0rd-9999", portal: "erp", license_number: lic })).status === 200);

  // duplicate username
  w.go("user-detail"); await sleep(700);
  field("First Name").value = "Dup"; field("Last Name").value = "User"; field("Designation").value = "Accounts Exec"; field("Username").value = "priya" + tag; field("Work Email").value = `dup${tag}@t.test`; field("Mobile").value = "1";
  d.getElementById("uk_pwd").value = "Dup-Passw0rd-12345"; toasts.length = 0; saveBtn().click(); await sleep(1000);
  check("duplicate username is reported by the server", toasts.some(t => /already exists/.test(t)), toasts.join(";"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
