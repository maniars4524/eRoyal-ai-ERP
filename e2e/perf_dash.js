const puppeteer = require("puppeteer-core"); const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "PF" + tag, name: "Perf " + tag, admin_email: `pf${tag}@t.test`, admin_username: "pf" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "pf" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token);
  const t0 = Date.now(), log = [];
  page.on("request", r => { r._t = Date.now(); });
  page.on("requestfinished", r => { const u = r.url().replace(BASE, ""); if (/\/api\//.test(u)) log.push([Date.now() - t0, Date.now() - r._t, u.slice(0, 60)]); });
  await page.reload({ waitUntil: "domcontentloaded" });
  let first = null, body = null;
  for (let i = 0; i < 100; i++) { const st = await page.evaluate(() => ({ kpi: !!document.querySelector("#dashXBody .dx-kpis"), any: (document.querySelector("#dashXBody") || { innerHTML: "" }).innerHTML.length })); if (st.any && body === null) body = Date.now() - t0; if (st.kpi) { first = Date.now() - t0; break; } await new Promise(r => setTimeout(r, 100)); }
  console.log("anything in body at", body, "ms; KPI cards at", first, "ms");
  log.sort((a, b) => a[0] - b[0]).forEach(l => console.log(l.join("  ")));
  await b.close();
})();
