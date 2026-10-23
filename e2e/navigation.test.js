// Headless UI check: the top menu behaves as before (always visible, flyouts on click) and the dashboard has no Export / Refresh / New Shipment / New Org buttons.
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
  const org = await j("POST", "/api/organizations", { code: "NV" + tag.slice(-6).toUpperCase(), name: "Nav " + tag, admin_email: `nv${tag}@t.test`, admin_username: "nv" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "N" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nv" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2800);
  const css = Array.from(d.querySelectorAll("style")).map(s => s.textContent).join("\n");
  const rows = () => Array.from(d.querySelectorAll("#topnavMenu > .tn-item > .tn-row"));

  check("there is no MENU toggle button: the menu bar is as it was", !d.getElementById("menuToggle") && !d.querySelector(".hdr-menu-btn"));
  check("the menu bar is a normal always-visible bar (sticky, no hidden state)", /\.topnav-wrap\{background:[^}]*position:sticky/.test(css) && !/\.topnav-wrap\{background:[^}]*display:none/.test(css) && !/\.topnav-wrap\.open/.test(css));
  check("the menu still lists its top-level groups", rows().length >= 6, String(rows().length));
  const parent = rows().find(r => r.dataset.panelId);
  parent.click(); await sleep(250);
  check("clicking a group opens its submenu", d.querySelectorAll(".tn-panel.on").length >= 1);
  const leaf = d.querySelector(".tn-panel.on .tn-row[data-go]");
  leaf.click(); await sleep(400);
  check("picking an item opens its screen and the submenu closes (as before)", d.querySelectorAll(".tn-panel.on").length === 0 && d.querySelector(".scr.on").id === "s-" + leaf.dataset.go, d.querySelector(".scr.on").id);

  w.go("dashboard"); await sleep(400);
  const dash = d.getElementById("s-dashboard");
  check("the dashboard has no Export, Refresh or New Shipment button", !dash.querySelector('[data-export="dashboard"]') && !d.getElementById("dashRefreshBtn") && !/New Shipment/.test(dash.querySelector(".ph").textContent) && !/Export|Refresh/.test(dash.querySelector(".ph").textContent), dash.querySelector(".ph").textContent.replace(/\s+/g, " "));
  check("the top bar has no duplicate New Org / New Job buttons (each list has its own)", !d.querySelector('.bc-actions [data-go="org-new"]') && !d.querySelector('.bc-actions [data-go="job-new"]'));
  check("the page starts with the dashboard flag so New Org is hidden from the first paint", /<body data-screen="dashboard">/.test(await (await fetch(BASE + "/erp")).text()));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
