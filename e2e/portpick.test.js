// Real Chrome: a port field offers its suggestions in a drop-down that a real mouse click, the arrow keys + Enter, and Tab all choose from.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "PP" + tag.slice(-6).toUpperCase(), name: "PortPick " + tag, admin_email: `pp${tag}@t.test`, admin_username: "pp" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "pp" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const have = await j("GET", "/api/ports?limit=3&status=ACTIVE&q=chennai", null, token);
  if (!have.body || !have.body.length) { for (const p of [{ code: "INMAA", unlocode: "INMAA", name: "Chennai (Madras)", port_type: "Sea Port", country: "India", city: "Chennai" }, { code: "INMAA4", unlocode: "INMAA4", name: "Chennai Airport", port_type: "Airport", country: "India", city: "Chennai" }]) await j("POST", "/api/ports", p, dev.body.access_token); }
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await (await browser.createBrowserContext()).newPage(); await page.setViewport({ width: 1440, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" });
  await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token);
  await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  await page.evaluate(() => window.go("route-new")); await sleep(1500);
  const field = "#route_origin";
  await page.click(field); await page.keyboard.type("chen", { delay: 60 }); await sleep(1200);
  const items = await page.$$eval("#ppBox .pp-item", els => els.map(e => e.textContent));
  check("typing part of a port name or code shows the suggestions", items.length >= 1, JSON.stringify(items));
  check("the native browser list is not used", await page.$eval(field, e => !e.hasAttribute("list")));
  const el = await page.$("#ppBox .pp-item"); const box = await el.boundingBox();
  await page.mouse.click(box.x + 20, box.y + box.height / 2);              // a real mouse click on the first suggestion
  await sleep(500);
  const chosen = await page.$eval(field, e => e.value);
  check("clicking a suggestion with the mouse fills the field with CODE(NAME)", /^[A-Z0-9]{3,6}\(.+\)$/.test(chosen), chosen);
  check("the drop-down closes after the choice and does not come back", (await page.$("#ppBox")) === null && (await sleep(700), (await page.$("#ppBox")) === null));
  // keyboard
  await page.$eval(field, e => { e.value = ""; e.focus(); }); await page.keyboard.type("chen", { delay: 60 }); await sleep(1200);
  await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await sleep(400);
  const kb = await page.$eval(field, e => e.value);
  check("arrow keys + Enter choose a suggestion", /^[A-Z0-9]{3,6}\(.+\)$/.test(kb), kb);
  // typed code and Tab: the code is completed to CODE(NAME)
  await page.$eval(field, e => { e.value = ""; e.focus(); }); await page.keyboard.type("INMAA", { delay: 60 }); await sleep(900); await page.keyboard.press("Tab"); await sleep(1500);
  const typed = await page.$eval(field, e => e.value);
  check("a typed code followed by Tab becomes CODE(NAME)", /^[A-Z0-9]{3,6}\(.+\)$/.test(typed), typed);
  // clicking elsewhere closes the list
  await page.$eval(field, e => { e.value = ""; e.focus(); }); await page.keyboard.type("chen", { delay: 60 }); await sleep(1000);
  await page.mouse.click(5, 5); await sleep(300);
  check("clicking elsewhere closes the list", (await page.$("#ppBox")) === null);
  await browser.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
