// The user menu shows the signed-in user from the user master (no made-up name) with the real roles, and View as CHA / Transport show CRM, Booking, their own service and Accounts.
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
  const org = await j("POST", "/api/organizations", { code: "UM" + tag.slice(-6).toUpperCase(), name: "Usermenu " + tag, admin_email: `um${tag}@t.test`, admin_username: "um" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Meena", admin_last_name: "Rao" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "um" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(7000);
  check("the header shows the signed-in user's name from the master", d.querySelector(".hdr-uname").textContent === "Meena Rao", d.querySelector(".hdr-uname").textContent);
  check("the user menu shows the same name and the real role (no 'Raj Kumar')", d.getElementById("hdrPopName").textContent === "Meena Rao" && /Admin/.test(d.getElementById("hdrPopRole").textContent) && !/Raj Kumar/.test(d.body.textContent), d.getElementById("hdrPopRole").textContent);
  check("the header role reads Admin, not ERP_ADMIN", /^Admin/.test(d.getElementById("userRoleLbl").textContent), d.getElementById("userRoleLbl").textContent);
  const roles = Array.from(d.querySelectorAll(".hdr-menu-item[data-role]")).map(x => x.dataset.role);
  check("View as offers CHA and Transport as well", ["Admin", "Operations", "Accounts", "Sales", "CHA", "Transport"].every(r => roles.includes(r)), roles.join());
  const tops = () => Array.from(d.querySelectorAll("#topnavMenu > .tn-item > .tn-row .tn-label")).map(x => x.textContent.trim());
  const subs = () => Array.from(d.querySelectorAll(".tn-panel .tn-label")).map(x => x.textContent.trim());
  for (const [role, own, other] of [["CHA", "CHA", "Transport"], ["Transport", "Transport", "CHA"]]) {
    d.querySelector(`.hdr-menu-item[data-role="${role}"]`).click(); await sleep(500);
    const t = tops();
    check(`View as ${role}: CRM, Logistics & Customs (with Booking), Accounts and Dashboard show; Masters and Settings do not`, ["Dashboard", "CRM", "Logistics & Customs", "Accounts"].every(x => t.includes(x)) && !t.includes("Masters") && !t.includes("Settings"), t.join());
    const s = subs();
    check(`View as ${role}: Logistics & Customs has Booking and only ${own}`, s.includes(own) && !s.includes(other) && !s.includes("Shipment Console") && !s.includes("Warehouse") && s.includes("Booking") && !s.includes("Job / Shipment") && !s.includes("All Job List"), s.join("|"));
  }
  d.querySelector('.hdr-menu-item[data-role="Admin"]').click(); await sleep(400);
  check("View as Admin brings everything back", tops().includes("Masters") && tops().includes("Settings"));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
