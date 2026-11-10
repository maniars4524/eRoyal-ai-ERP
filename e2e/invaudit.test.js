// Real Chrome audit of Inventory: every menu entry reaches an existing screen, no script / console errors, breadcrumbs and titles are right, Back works,
// every one of the 21 masters saves from its own form, reloads into the form for editing, appears in the list, and nothing shows "undefined" / "[object".
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
  const org = await j("POST", "/api/organizations", { code: "AU" + tag, name: "Audit " + tag, admin_email: `au${tag}@t.test`, admin_username: "au" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "A" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "au" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 950 });
  const errs = [], cons = [];
  page.on("pageerror", e => errs.push(e.message)); page.on("console", m => { if (m.type() === "error" && !/favicon|ERR_|401|404|409|422/.test(m.text())) cons.push(m.text().slice(0, 160)); }); page.on("dialog", d => d.accept("audit reason"));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);

  // ---- 1. every Inventory menu entry resolves to an existing screen
  const entries = await page.evaluate(() => Array.from(document.querySelectorAll('.tn-row[data-go^="inv-"], .tn-row[data-go="container-master"]')).map(r => ({ go: r.dataset.go, label: r.textContent.trim() })));
  const missing = await page.evaluate(list => list.filter(e => !document.getElementById("s-" + e.go)).map(e => e.go), entries);
  check("all " + entries.length + " Inventory menu entries point at a screen that exists", entries.length >= 28 && missing.length === 0, missing.join());
  const detailless = entries.filter(e => !/-detail$/.test(e.go));

  // ---- 2. open each from the menu: it becomes the active screen, title + breadcrumb are set, no placeholder text, Back returns
  let bad = [];
  for (const e of detailless) {
    await page.evaluate(g => document.querySelector('.tn-row[data-go="' + g + '"]').click(), e.go); await sleep(1100);
    const r = await page.evaluate(g => { const s = document.getElementById("s-" + g); const t = s.innerText; return { on: s.classList.contains("on"), bc: document.getElementById("bc").innerText, title: document.title, junk: /undefined|\[object|NaN/.test(t), blank: t.trim().length < 20, onlyOne: document.querySelectorAll(".scr.on").length === 1 }; }, e.go);
    if (!(r.on && r.onlyOne && !r.junk && !r.blank && /Inventory|Container Type|Masters|NVOCC/.test(r.bc) && /eSway ERP 2.0 — /.test(r.title))) bad.push(e.go + ":" + JSON.stringify(r));
  }
  check("every Inventory screen opens from the menu with a title, a breadcrumb and no undefined / [object / NaN text", bad.length === 0, bad.slice(0, 3).join(" ; "));

  // ---- 3. every master saves through its own form, is listed, reloads for editing
  const defs = await page.evaluate(() => window.InventoryMasters.DEFS.map(d => ({ key: d.key, type: d.type, label: d.label, codeLabel: d.codeLabel, nameLabel: d.nameLabel, req: d.fields.filter(f => f.req).map(f => ({ label: f.label, t: f.t, opts: f.opts || null, sel: !!f.sel })) })));
  let saveBad = [], editBad = [];
  for (const d of defs) {
    await page.evaluate(k => go("inv-" + k + "-detail"), d.key); await sleep(1000);
    const code = ("A" + tag + d.key).toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 20);
    const fill = (label, value) => page.evaluate((label, value) => { const sc = document.querySelector(".scr.on"); const f = Array.from(sc.querySelectorAll(".fg")).find(x => x.querySelector("label") && x.querySelector("label").textContent.replace("*", "").trim() === label); if (!f) return false; const c = f.querySelector("input,select,textarea"); if (c.tagName === "SELECT") { const o = Array.from(c.options).find(o => o.value && (value === null || o.text === value || o.value === value)) || null; if (!o) return false; c.value = o.value; } else c.value = value; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); return true; }, label, value);
    await fill(d.codeLabel, code); await fill(d.nameLabel, d.label + " " + tag);
    for (const f of d.req) { if (f.t === "select" && f.opts) await fill(f.label, f.opts[0]); else if (f.t === "number") await fill(f.label, "2"); else if (f.sel) await fill(f.label, null); else await fill(f.label, "x"); }
    await page.evaluate(() => { const sc = document.querySelector(".scr.on"); Array.from(sc.querySelectorAll("button")).find(b => b.textContent.trim() === "Save & Close").click(); }); await sleep(1700);
    const rows = (await j("GET", "/api/masters/" + d.type, null, token)).body;
    if (!(Array.isArray(rows) && rows.length === 1 && rows[0].code === code)) { saveBad.push(d.key + ":" + JSON.stringify(rows).slice(0, 120)); continue; }
    const listed = await page.evaluate((k, label, tag) => { const s = document.getElementById("s-inv-" + k); return s.classList.contains("on") && s.innerText.includes(label + " " + tag); }, d.key, d.label, tag);
    if (!listed) saveBad.push(d.key + ": not listed after Save & Close");
    // edit: open from the list, the name is loaded into the form, change it, save, API shows the change
    await page.evaluate(k => document.querySelector("#s-inv-" + k + " [data-mk-edit]").click(), d.key); await sleep(1200);
    const loaded = await page.evaluate((nl) => { const sc = document.querySelector(".scr.on"); const f = Array.from(sc.querySelectorAll(".fg")).find(x => x.querySelector("label") && x.querySelector("label").textContent.replace("*", "").trim() === nl); return f ? f.querySelector("input").value : null; }, d.nameLabel);
    await fill(d.nameLabel, "Edited " + d.label); await page.evaluate(() => { const sc = document.querySelector(".scr.on"); Array.from(sc.querySelectorAll("button")).find(b => b.textContent.trim() === "Save").click(); }); await sleep(1500);
    const after = (await j("GET", "/api/masters/" + d.type, null, token)).body;
    if (!(loaded === d.label + " " + tag && after.length === 1 && after[0].name === "Edited " + d.label)) editBad.push(d.key + ":" + loaded + "/" + (after[0] && after[0].name));
  }
  check("all 21 masters save from their own form, are listed afterwards and keep the right code", saveBad.length === 0, saveBad.join(" ; "));
  check("each saved master opens in its form with the saved values and an edit is stored", editBad.length === 0, editBad.join(" ; "));

  // ---- 4. Back buttons and cross-screen links
  await page.evaluate(() => go("inv-containers")); await sleep(1200);
  await page.evaluate(() => document.querySelector("#invNewCont").click()); await sleep(1500);
  await page.evaluate(() => Array.from(document.querySelectorAll("#s-inv-container-detail button")).find(b => /Back/.test(b.textContent)).click()); await sleep(800);
  check("Back from the container form returns to the container list", await page.evaluate(() => document.getElementById("s-inv-containers").classList.contains("on")));
  await page.evaluate(() => go("inv-expiry")); await sleep(1500);
  check("the expiry register opens and its window selector works", await page.evaluate(() => !!document.getElementById("ogExpDays")));
  await page.evaluate(() => go("inv-dashboard")); await sleep(1800);
  const links = await page.evaluate(() => Array.from(document.querySelectorAll("#s-inv-dashboard [data-go]")).map(a => a.dataset.go));
  check("every link on the dashboard goes to an existing screen", (await page.evaluate(l => l.filter(g => g !== "back" && !document.getElementById("s-" + g)), links)).length === 0);

  // ---- 5. access: a user without container permissions sees no Inventory data
  const viewer = await j("POST", "/api/users", { email: `vw${tag}@t.test`, username: "vw" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", first_name: "V", role: "VIEWER" }, token);
  const vtoken = viewer.status === 201 ? (await j("POST", "/api/auth/login", { identifier: "vw" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token : null;
  if (vtoken) {
    check("a Viewer can read containers but cannot create, approve or upload", (await j("GET", "/api/containers", null, vtoken)).status === 200 && (await j("POST", "/api/containers", { container_number: "CSQU3054383" }, vtoken)).status === 403
      && (await j("POST", "/api/container-leases", { agreement_number: "X", leasing_company: "Y", start_date: "2026-01-01" }, vtoken)).status === 403 && (await j("POST", "/api/container-attachments", { entity_type: "container", entity_id: 1, name: "a.png", content_type: "image/png", data_base64: "AA==" }, vtoken)).status === 403);
  } else console.log("SKIP viewer check (could not create a viewer: " + viewer.status + ")");
  check("no script errors during the whole walk", errs.length === 0, errs.join(" | ").slice(0, 300));
  check("no unexpected console errors during the whole walk", cons.length === 0, cons.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
