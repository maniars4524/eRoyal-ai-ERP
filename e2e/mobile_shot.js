// Takes phone-size screenshots (390 x 844) of the login page and main screens, for a visual check of the mobile layout. Usage: node mobile_shot.js [outdir]
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const OUT = process.argv[2] || "C:/Users/hp/AppData/Local/Temp/mobile";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "MB" + tag.slice(-6).toUpperCase(), name: "Mobile Test " + tag, admin_email: `mb${tag}@t.test`, admin_username: "mb" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mo" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "mb" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "USLAX" }, token);
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await page.setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1");
  const shot = async name => { await page.screenshot({ path: OUT + "/" + name + ".png" }); console.log("shot", name); };
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await sleep(1500); await shot("1-login");
  const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  console.log("login horizontal overflow:", overflow.sw > overflow.cw + 1, overflow);
  await page.evaluateOnNewDocument(t => { sessionStorage.setItem("esway_access_token", t); localStorage.setItem("esway_persistent_token", t); }, token);
  await page.goto(BASE + "/erp", { waitUntil: "networkidle2" }); await sleep(5000); await shot("2-dashboard");
  const screens = process.argv.slice(3).length ? process.argv.slice(3) : ["job-list", "job-new", "org-list", "quotation", "booking"];
  for (const s of screens) { await page.evaluate(x => window.go(x), s); await sleep(1800); await shot("3-" + s); const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })); console.log(s, "horizontal overflow:", o.sw > o.cw + 1, o); }
  await browser.close();
})().catch(e => { console.error("ERROR", e); process.exit(2); });
