const puppeteer = require("puppeteer-core"); const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "SK" + tag, name: "Sk " + tag, admin_email: `sk${tag}@t.test`, admin_username: "sk" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M", admin_last_name: "R" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sk" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  for (let i = 0; i < 3; i++) { const d = (await j("POST", "/api/documents", { doc_type: "invoice", party_name: "P" + i + " " + tag, supply_type: "intra", lines: [{ charge_name: "X", qty: "1", rate: String(1000 * (i + 1)), tax_type: "Taxable", tax_pct: "18" }] }, token)).body; await j("POST", `/api/documents/${d.id}/post`, null, token); }
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 });
  await page.evaluateOnNewDocument(() => {
    const NL = String.fromCharCode(10); window.__hits = {};
    const where = () => (new Error().stack || "").split(NL).slice(5, 12).map(x => x.trim().replace(/http:[^ ]*[/]/, "")).join(" | ");
    const d = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
    Object.defineProperty(Element.prototype, "innerHTML", { set(v) { if (this.tagName === "TBODY") { const k = "INNERHTML " + where(); window.__hits[k] = (window.__hits[k] || 0) + 1; } return d.set.call(this, v); }, get() { return d.get.call(this); }, configurable: true });
    const ap = Node.prototype.appendChild; Node.prototype.appendChild = function (n) { if (this.tagName === "TR") { const k = "APPEND " + where(); window.__hits[k] = (window.__hits[k] || 0) + 1; } return ap.call(this, n); };
  });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await new Promise(r => setTimeout(r, 3000));
  await page.evaluate(() => window.go("invoice-list")); await new Promise(r => setTimeout(r, 6000));
  console.log(JSON.stringify(await page.evaluate(() => Object.entries(window.__hits).sort((a, b) => b[1] - a[1]).slice(0, 6)), null, 1));
  await b.close();
})();
