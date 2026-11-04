// Real Chrome, phone size: the menu drawer ends with Sign out under Settings; tapping it signs the user out. Desktop does not show it.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "MS" + tag, name: "Mob " + tag, admin_email: `ms${tag}@t.test`, admin_username: "ms" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ms" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const open = async vp => { const page = await (await b.createBrowserContext()).newPage(); await page.setViewport(vp); await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500); return page; };
  const phone = await open({ width: 390, height: 800, isMobile: true, hasTouch: true });
  await phone.click("#navBurger"); await sleep(600);
  const info = await phone.evaluate(() => { const so = document.getElementById("tnSignOut"), r = so.getBoundingClientRect(), fly = document.getElementById("topnavMenu").getBoundingClientRect(); return { shown: getComputedStyle(so).display !== "none", inView: r.bottom <= innerHeight + 1 && r.top >= 0, belowMenu: r.top >= fly.bottom - 1, text: so.textContent.trim() }; });
  check("phone: Sign out is visible in the menu drawer, below the menu items", info.shown && info.belowMenu && info.text === "Sign out", JSON.stringify(info));
  await phone.screenshot({ path: "../scratch/mobile-menu-signout.png" });
  await phone.click("#tnSignOut"); await sleep(2500);
  const after = await phone.evaluate(() => ({ url: location.pathname, token: localStorage.getItem("esway_persistent_token"), session: sessionStorage.getItem("esway_access_token") }));
  check("tapping it signs out: back on the login page and the saved sign-in is gone", /\/login$/.test(after.url) && !after.token && !after.session, JSON.stringify(after));
  const desk = await open({ width: 1400, height: 900 });
  check("desktop: the extra Sign out row is not shown", await desk.evaluate(() => getComputedStyle(document.getElementById("tnSignOut")).display === "none"));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
