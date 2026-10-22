const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "RS" + tag.slice(-5).toUpperCase(), name: "Rs " + tag, admin_email: `rs${tag}@t.test`, admin_username: "rs" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Rs" }, dev.body.access_token);
  const tok = (await j("POST", "/api/auth/login", { identifier: "rs" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const vc = new VirtualConsole(); vc.on("error", (...a) => console.log("CONSOLE.ERROR", a.map(String).join(" ").slice(0, 600))); vc.on("jsdomError", e => { if (!/scrollTo|navigation/i.test(e.message)) console.log("JSDOMERR", String(e.message).slice(0, 300)); });
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.localStorage.setItem("esway_persistent_token", tok); } });
  await new Promise(r => setTimeout(r, 7000));
  const ov = dom.window.document.getElementById("loginOverlay");
  console.log("overlay hidden:", ov.classList.contains("hidden") || ov.style.display === "none");
  process.exit(0);
})();
