// Runtime navigation audit: open every screen through go(), check it becomes the visible screen, has a title and breadcrumb, and throws no script error.
const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "NV" + tag.slice(-5).toUpperCase(), name: "Nav " + tag, admin_email: `nv${tag}@t.test`, admin_username: "nv" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Nv" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nv" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  let errs = [];
  const vc = new VirtualConsole(); vc.on("jsdomError", e => { if (!/scrollTo|getContext|navigation|setTransform|createObjectURL/i.test(String(e.message))) errs.push(String(e.message).slice(0, 160)); });
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(3500);
  const ids = [...d.querySelectorAll(".scr")].map(s => s.id.replace(/^s-/, ""));
  const bad = [];
  for (const id of ids) {
    errs = [];
    try { w.go(id); } catch (e) { bad.push(id + ": go() threw " + e.message); continue; }
    await sleep(500);
    const on = d.querySelector(".scr.on");
    const crumb = (d.getElementById("bc") || {}).textContent || "";
    const problems = [];
    if (!on || on.id !== "s-" + id) problems.push("screen not shown (shown: " + (on ? on.id : "none") + ")");
    if (!crumb.trim() || crumb.trim() === id) problems.push("no breadcrumb title");
    if (errs.length) problems.push("script error: " + errs[0]);
    if (problems.length) bad.push(id + ": " + problems.join("; "));
  }
  console.log("screens opened:", ids.length);
  console.log(bad.length ? "PROBLEMS:\n  " + bad.join("\n  ") : "no problems");
  process.exit(0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
