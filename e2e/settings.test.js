// Settings menu: every option opens something real (no org list / SOP page standing in), number systems open Sequence Setting, license information shows the company's license.
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
  const org = await j("POST", "/api/organizations", { code: "ST" + tag.slice(-6).toUpperCase(), name: "Settings " + tag, admin_email: `st${tag}@t.test`, admin_username: "st" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Sia" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const token = (await j("POST", "/api/auth/login", { identifier: "st" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(7000);
  const click = async label => { const r = Array.from(d.querySelectorAll(".tn-row")).find(x => x.querySelector(".tn-label") && x.querySelector(".tn-label").textContent.trim() === label && x.dataset && (x.dataset.go || x.dataset.act)); if (!r) return null; r.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(900); return r; };
  const on = () => (d.querySelector(".scr.on") || {}).id;
  check("Sequence Setting (number systems) opens its screen", !!(await click("Sequence Setting")) && on() === "s-sequence-setting", on());
  check("User Management opens the User Master", !!(await click("User Management")) && on() === "s-user-master", on());
  check("Organisation Settings opens the company documents profile", !!(await click("Organisation Settings")) && d.getElementById("modalOvl").classList.contains("on") && /Company|profile|Logo/i.test(d.getElementById("modalBody").textContent), d.getElementById("modalTitle").textContent);
  w.closeModal();
  check("License Information shows this company's license", !!(await click("License Information")) && d.getElementById("modalBody").textContent.includes(lic), d.getElementById("modalBody").textContent.slice(0, 120));
  w.closeModal();
  check("Email Configuration says honestly that it is not set up", !!(await click("Email Configuration")) && /SMTP/.test(d.getElementById("modalBody").textContent));
  w.closeModal();
  check("Themes opens the themes screen", !!(await click("Themes")) && on() === "s-themes", on());
  w.go("entcfg"); await sleep(500);
  const card = Array.from(d.querySelectorAll(".ecitem")).find(x => x.dataset.name === "Number Series Configuration");
  card.click(); await sleep(700);
  check("the Enterprise Configuration card 'Number Series Configuration' opens Sequence Setting", on() === "s-sequence-setting", on());
  w.go("entcfg"); await sleep(400);
  Array.from(d.querySelectorAll(".ecitem")).find(x => x.dataset.name === "Backup Configuration").click(); await sleep(500);
  check("an unavailable card shows a note with Close only (no fake Save)", /not available yet/.test(d.getElementById("drawerBody") ? d.getElementById("drawerBody").textContent : d.body.textContent) && !/Save Configuration/.test(d.body.textContent.split("Backup Configuration").pop() || ""));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
