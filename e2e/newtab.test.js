// Real Chrome: Ctrl+click / middle click / right-click "Open in New Tab" opens the exact screen in a signed-in new tab; the original tab is untouched; a direct address asks for sign-in.
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
  const org = await j("POST", "/api/organizations", { code: "NT" + tag.slice(-6).toUpperCase(), name: "NewTab " + tag, admin_email: `nt${tag}@t.test`, admin_username: "nt" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nt" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage(); await page.setViewport({ width: 1440, height: 900 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" });
  await page.evaluate(t => { localStorage.setItem("esway_persistent_token", t); }, token);
  await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  check("the first tab is signed in", await page.evaluate(() => !!sessionStorage.getItem("esway_access_token")));
  await page.evaluate(() => localStorage.removeItem("esway_persistent_token"));        // an ordinary sign-in: no Remember me
  await page.evaluate(() => window.go("job-list")); await sleep(600);
  const before = await page.evaluate(() => document.body.dataset.screen);
  // Ctrl+click on a menu item (window.open is captured: the new tab itself is opened below the way a browser does)
  const selector = '[data-go="job-list"][data-jobdir="Import"]';
  await page.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });
  await page.evaluate(sel => { const el = document.querySelector(sel); el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true })); }, selector);
  const opened = await page.evaluate(() => window.__opened);
  check("Ctrl+click opens the exact screen with its filter in a new tab, and the address holds no token", opened.length === 1 && /^\/erp#job-list~/.test(opened[0]) && /jobdir/.test(decodeURIComponent(opened[0])) && !/eyJ/.test(decodeURIComponent(opened[0])), opened[0]);
  const handoff = await page.evaluate(() => localStorage.getItem("esway_tab_handoff"));
  check("the session is handed over once through browser storage", !!handoff && JSON.parse(handoff).t === token);
  const p2 = await ctx.newPage(); await p2.setViewport({ width: 1440, height: 900 });
  await p2.goto(BASE + opened[0], { waitUntil: "networkidle2" }); await sleep(6500);
  const st = await p2.evaluate(() => ({ screen: document.body.dataset.screen, token: !!sessionStorage.getItem("esway_access_token"), dir: (document.getElementById("jobDirFilter") || {}).value }));
  check("the new tab is signed in and shows the selected screen", st.token && st.screen === "job-list", JSON.stringify(st));
  check("the menu item's filter (Import) came along", st.dir === "Import", JSON.stringify(st));
  check("the original tab is unchanged", (await page.evaluate(() => document.body.dataset.screen)) === before && (await page.evaluate(() => !!sessionStorage.getItem("esway_access_token"))));
  check("the new tab consumed the hand-over", (await p2.evaluate(() => localStorage.getItem("esway_tab_handoff"))) === null);
  await p2.reload({ waitUntil: "networkidle2" }); await sleep(4000);
  const re = await p2.evaluate(() => ({ screen: document.body.dataset.screen, token: !!sessionStorage.getItem("esway_access_token") }));
  check("refreshing the new tab keeps the user signed in on the same screen", re.token && re.screen === "job-list", JSON.stringify(re));
  // right-click menu
  await page.bringToFront();
  await page.evaluate(sel => { const el = document.querySelector(sel); el.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 200 })); }, selector);
  check("right-click on a menu item offers Open in New Tab", await page.evaluate(() => !!document.getElementById("ntMenu")));
  await page.evaluate(() => { window.__opened = []; });
  await page.evaluate(() => document.querySelector('#ntMenu [data-nt="open"]').click());
  check("choosing it opens the screen in a new tab", (await page.evaluate(() => window.__opened)).length === 1);
  // a job record in a new tab
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "NT Co", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "CHA", pcs: "5", consignee: "X" } }, token)).body;
  await j("POST", `/api/bookings/${bk.id}/confirm`, null, token);
  const cj = (await j("POST", `/api/bookings/${bk.id}/jobs`, { service: "CHA" }, token)).body.created_job;
  const rec = await page.evaluate(job => { const d = document.createElement("div"); d.dataset.go = "cha-job"; d.dataset.job = job; d.style.display = "none"; document.body.appendChild(d); return window.NewTab.urlFor(d); }, cj);
  check("a job opened in a new tab keeps its job number in the address", decodeURIComponent(rec).includes(cj), rec);
  const p3 = await ctx.newPage(); await page.evaluate(() => localStorage.setItem("esway_tab_handoff", JSON.stringify({ t: sessionStorage.getItem("esway_access_token"), exp: Date.now() + 20000 })));
  await p3.goto(BASE + rec, { waitUntil: "networkidle2" }); await sleep(6000);
  const s3 = await p3.evaluate(() => ({ screen: document.body.dataset.screen, has: !!document.getElementById("chaJobRoot") && document.getElementById("chaJobRoot").textContent.length > 50 }));
  check("the job screen opens directly in the new tab", s3.screen === "cha-job" && s3.has, JSON.stringify(s3));
  // no session at all: sign-in first
  const ctx2 = await browser.createBrowserContext(); const p4 = await ctx2.newPage();
  await p4.goto(BASE + rec, { waitUntil: "networkidle2" }); await sleep(2500);
  check("opened without a session the address asks for sign-in", await p4.evaluate(() => !sessionStorage.getItem("esway_access_token") && getComputedStyle(document.getElementById("loginOverlay")).display !== "none"));
  await browser.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
