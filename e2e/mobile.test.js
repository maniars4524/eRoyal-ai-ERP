// Real-browser check (Chrome) that the ERP fits phones and tablets and that desktop is unchanged: no horizontal overflow, hamburger drawer on phones, drawer closes after a choice,
// no new console errors, PWA manifest / service worker / icons are served. Needs the QA server on :8010.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
(async () => {
  // PWA pieces
  const man = await (await fetch(BASE + "/manifest.webmanifest")).json();
  check("the web app manifest is served (standalone, start /erp, 192 + 512 + maskable icons)", man.display === "standalone" && man.start_url === "/erp" && man.icons.some(i => i.sizes === "192x192") && man.icons.some(i => i.sizes === "512x512") && man.icons.some(i => i.purpose === "maskable"), JSON.stringify(man).slice(0, 120));
  const sw = await fetch(BASE + "/sw.js");
  check("the service worker is served with scope /", sw.status === 200 && sw.headers.get("service-worker-allowed") === "/" && /\/api\//.test(await sw.text()));
  check("the icons and the offline page are served", (await Promise.all(["icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "offline.html"].map(n => fetch(BASE + "/pwa/" + n)))).every(r => r.status === 200));
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "MB" + tag.slice(-6).toUpperCase(), name: "Mobile " + tag, admin_email: `mb${tag}@t.test`, admin_username: "mb" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const lic = org.body.organization.license_number;
  const token = (await j("POST", "/api/auth/login", { identifier: "mb" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic })).body.access_token;
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Mob Co", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "CHA", pcs: "5", consignee: "B" } }, token)).body;
  await j("POST", `/api/bookings/${bk.id}/confirm`, null, token);
  const cj = (await j("POST", `/api/bookings/${bk.id}/jobs`, { service: "CHA" }, token)).body.created_job;
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const overflow = page => page.evaluate(() => { const cw = document.documentElement.clientWidth; return { sw: document.documentElement.scrollWidth, cw }; });
  const sizes = (process.env.SIZES ? JSON.parse(process.env.SIZES) : [[320, 640, true], [360, 740, true], [375, 667, true], [390, 844, true], [414, 896, true], [768, 1024, true], [844, 390, true], [1440, 900, false]]);
  for (const [w, h, mobile] of sizes) {
    const ctx = await browser.createBrowserContext(); const page = await ctx.newPage(); const errors = [];
    page.on("pageerror", e => errors.push(String(e.message).slice(0, 120)));
    await page.setViewport({ width: w, height: h, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 1 });
    await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await sleep(1200);
    const lo = await overflow(page);
    check(`${w}x${h} login page has no horizontal overflow`, lo.sw <= lo.cw + 1, JSON.stringify(lo));
    await page.type("#loginLicense", lic); await page.type("#loginUser", "mb" + tag); await page.type("#loginPass", "Ui-Test-Passw0rd-1"); await page.keyboard.press("Enter"); await sleep(6500);
    const phone = w <= 760;
    const burger = await page.evaluate(() => { const b = document.getElementById("navBurger"); return b ? getComputedStyle(b).display : "none"; });
    check(`${w}x${h} hamburger ${phone ? "is shown on phones" : "is hidden on larger screens"}`, phone ? burger !== "none" : burger === "none", burger);
    for (const sc of ["dashboard", "svc-cha", "cha-job:" + cj, "invoice-new", "booking-new", "job-list"]) {
      const [name, arg] = sc.split(":");
      await page.evaluate((n, a) => window.go(n, a ? { jobNo: a } : undefined), name, arg); await sleep(2200);
      const o = await overflow(page);
      check(`${w}x${h} ${name} fits the screen (no horizontal scroll)`, o.sw <= o.cw + 1, JSON.stringify(o));
    }
    if (phone) {
      await page.evaluate(() => window.go("dashboard")); await sleep(800);
      const before = await page.evaluate(() => document.body.className + "|" + (document.querySelector(".scr.on") || {}).id);
      await page.click("#navBurger"); await sleep(400);
      const open = await page.evaluate(() => { const wrap = document.querySelector(".topnav-wrap"); const r = wrap.getBoundingClientRect(); return { on: document.body.classList.contains("nav-open"), shown: getComputedStyle(wrap).display !== "none", w: Math.round(r.width), vw: window.innerWidth, cls: document.body.className, scr: (document.querySelector(".scr.on") || {}).id }; });
      open.before = before; open.hit = await page.evaluate(() => { const b = document.getElementById('navBurger'); const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return (e ? e.tagName + '#' + e.id + '.' + (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className) : 'none') + ' rect ' + [r.x, r.y, r.width, r.height].join(','); }); await page.screenshot({ path: 'C:/Users/hp/AppData/Local/Temp/mobile/fail-' + w + '.png' }); open.burgers = await page.evaluate(() => document.querySelectorAll('#navBurger').length); open.sw = await page.evaluate(() => navigator.serviceWorker && navigator.serviceWorker.controller ? 'controlled' : 'none');
      check(`${w}x${h} the hamburger opens the menu drawer (not wider than the screen)`, open.on && open.shown && open.w <= open.vw, JSON.stringify(open));
      await page.evaluate(() => { const r = Array.from(document.querySelectorAll(".topnav-flyout > .tn-item > .tn-row")).find(x => /Dashboard/.test(x.textContent)); r.click(); }); await sleep(600);
      check(`${w}x${h} choosing a menu item closes the drawer`, await page.evaluate(() => !document.body.classList.contains("nav-open")));
      await page.click("#navBurger"); await sleep(300); await page.keyboard.press("Escape"); await sleep(300);
      check(`${w}x${h} Escape closes the drawer`, await page.evaluate(() => !document.body.classList.contains("nav-open")));
    } else {
      check(`${w}x${h} the desktop menu bar is still the horizontal bar`, await page.evaluate(() => { const f = document.querySelector(".topnav-flyout"); return getComputedStyle(f).flexDirection === "row"; }));
    }
    const real = errors.filter(e => !/setTransform|getContext/.test(e));
    check(`${w}x${h} no new script errors in the console`, real.length === 0, real.join(" | "));
    await page.evaluate(() => fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + (sessionStorage.getItem('esway_access_token') || '') } })).catch(() => {}); await sleep(300);
    await ctx.close();
  }
  await browser.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
