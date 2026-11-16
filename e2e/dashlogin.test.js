// Sign in through the real login form: the new dashboard must fill in by itself (no skeleton left behind), quickly
const puppeteer = require("puppeteer-core"); const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
let failures = 0; const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "DL" + tag, name: "DL " + tag, admin_email: `dl${tag}@t.test`, admin_username: "dl" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.message)));
  for (const round of [1, 2]) {
    if (round === 2) { await page.evaluate(() => sessionStorage.clear()); }
    await page.goto(BASE + "/erp/login", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#loginUser", { visible: true });
    await page.evaluate((l, u) => { document.getElementById("loginLicense").value = l; document.getElementById("loginUser").value = u; document.getElementById("loginPass").value = "Ui-Test-Passw0rd-1"; document.getElementById("loginBtn").click(); }, lic, "dl" + tag.toLowerCase());
    const t0 = Date.now(); let ok = false;
    while (Date.now() - t0 < 15000) { ok = await page.evaluate(() => { const k = document.querySelector("#dashXBody .dx-kpi"); return !!k; }); if (ok) break; await new Promise(r => setTimeout(r, 100)); }
    check("round " + round + ": dashboard cards filled in after sign-in by themselves (" + (Date.now() - t0) + " ms)", ok);
    check("round " + round + ": no skeleton left", ok && (await page.evaluate(() => document.querySelectorAll("#dashXBody .dx-skel").length)) === 0);
  }
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
