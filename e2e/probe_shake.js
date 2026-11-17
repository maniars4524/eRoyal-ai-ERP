const puppeteer = require("puppeteer-core"); const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "SH" + tag, name: "Sh " + tag, admin_email: `sh${tag}@t.test`, admin_username: "sh" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M", admin_last_name: "R" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sh" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  for (let i = 0; i < 3; i++) { const d = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "P" + i + " " + tag, supply_type: "intra", lines: [{ charge_name: "X", qty: "1", rate: String(1000 * (i + 1)), tax_type: "Taxable", tax_pct: "18" }] }, token)).body; await j("POST", `/api/documents/${d.id}/post`, null, token); }
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 });
  page.on("pageerror", e => console.log("PAGEERR", e.message));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await new Promise(r => setTimeout(r, 3000));
  await page.evaluate(() => window.go("invoice-list")); await new Promise(r => setTimeout(r, 1500));
  const res = await page.evaluate(async () => {
    const scr = document.querySelector("#s-invoice-list"); let muts = 0; const kinds = {};
    const mo = new MutationObserver(l => { muts += l.length; l.forEach(m => { const t = (m.target.id || m.target.className || m.target.tagName || "").toString().slice(0, 40); kinds[t] = (kinds[t] || 0) + 1; }); }); mo.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
    const cards = () => [...scr.querySelectorAll(".card")].slice(0, 6).map(c => Math.round(c.getBoundingClientRect().top) + ":" + Math.round(c.getBoundingClientRect().height)).join(",");
    const samples = []; for (let i = 0; i < 12; i++) { samples.push(cards()); await new Promise(r => setTimeout(r, 500)); }
    mo.disconnect(); return { muts, top: Object.entries(kinds).sort((a, b) => b[1] - a[1]).slice(0, 6), distinctLayouts: new Set(samples).size, samples: samples.slice(0, 3) };
  });
  console.log(JSON.stringify(res, null, 1));
  await b.close();
})();
