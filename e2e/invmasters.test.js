// Real Chrome: Masters > Inventory. The menu group, all 21 masters open empty, a record is saved from the form, vendor organisations and other masters fill the pick-lists, import / export are there.
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
  const org = await j("POST", "/api/organizations", { code: "IM" + tag, name: "Inv " + tag, admin_email: `im${tag}@t.test`, admin_username: "im" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "I" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "im" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const vendor = (await j("POST", "/api/customers", { code: "VD" + tag, name: "Depot Operator " + tag, roles: ["transporter"] }, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 900 }); const errs = []; page.on("pageerror", e => errs.push(e.message));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);

  const menu = await page.evaluate(() => { const g = Array.from(document.querySelectorAll(".tn-label")).find(x => x.textContent.trim() === "Inventory"); const panel = g && g.closest(".tn-panel, .tn-group, div"); const rows = Array.from(document.querySelectorAll('.tn-row[data-go^="inv-"], .tn-row[data-go="container-master"]')); return { group: !!g, goes: rows.map(r => r.dataset.go) }; });
  check("Masters has an Inventory group", menu.group, JSON.stringify(menu).slice(0, 200));
  const master = menu.goes.filter(x => x.startsWith("inv-") && !["inv-dashboard", "inv-containers", "inv-movements", "inv-surveys", "inv-damages", "inv-repairs", "inv-leases", "inv-counts", "inv-expiry", "inv-fleet", "inv-vehicles", "inv-workshop", "inv-plans", "inv-shipment-template", "inv-tyres", "inv-fuel"].includes(x));
  check("the Inventory group lists at least the 22 entries (21 inventory masters + the existing Container Type; NVOCC and vehicle masters were added since)", master.length >= 21 && menu.goes.includes("container-master"), master.length + "");
  const groups = await page.evaluate(() => Array.from(document.querySelectorAll(".tn-label")).map(x => x.textContent.trim()));
  check("Masters > Inventory is split into Container Inventory, Vehicle Inventory and Other Inventory", ["Container Inventory", "Vehicle Inventory", "Other Inventory"].every(g => groups.includes(g)), groups.join("|").slice(0, 200));

  const keys = await page.evaluate(() => window.InventoryMasters.DEFS.map(d => d.key));
  check("the kit defines 21 masters", keys.filter(k => k !== "shipment-template").length === 21, keys.length + "");
  for (const k of keys.filter(k => k !== "shipment-template")) {
    await page.evaluate(k => document.querySelector('.tn-row[data-go="inv-' + k + '"]').click(), k); await sleep(900);
    const r = await page.evaluate(k => { const s = document.getElementById("s-inv-" + k); const rows = s.querySelectorAll("tbody tr:not(.mk-empty)").length; return { on: s.classList.contains("on"), rows, empty: /No records yet/.test(s.textContent), io: /Import/.test(s.textContent) && /Export/.test(s.textContent), title: document.title }; }, k);
    check(k + ": opens from the menu, empty (no sample rows), with Import / Export", r.on && r.rows === 0 && r.empty && r.io, JSON.stringify(r));
  }

  // a depot is entered on the form and saved to the database
  await page.evaluate(() => go("inv-depot-detail")); await sleep(1500);
  const opsList = await page.evaluate(() => Array.from(document.querySelectorAll("#s-inv-depot-detail .fg")).find(f => /Depot Operator/.test(f.textContent)).querySelectorAll("option").length);
  check("Depot Operator pick-list is filled from the Organisation Master vendors", opsList >= 2, opsList + "");
  const fill = async (label, value) => page.evaluate((label, value) => { const f = Array.from(document.querySelectorAll("#s-inv-depot-detail .fg")).find(x => x.querySelector("label") && x.querySelector("label").textContent.replace("*", "").trim() === label); const c = f.querySelector("input,select,textarea"); if (c.tagName === "SELECT") { const o = Array.from(c.options).find(o => o.text === value || o.value === value); c.value = o ? o.value : ""; } else c.value = value; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); }, label, value);
  await fill("Depot Code", "CHN-" + tag); await fill("Depot Name", "Chennai Depot " + tag); await fill("Depot Operator", vendor.name); await fill("City", "Chennai"); await fill("Total Capacity (TEU)", "1500"); await fill("Workshop Available", "Yes");
  check("the form has Save, Save & New and Save & Close", await page.evaluate(() => { const t = document.getElementById("s-inv-depot-detail").textContent; return /Save & New/.test(t) && /Save & Close/.test(t); }));
  await page.evaluate(() => Array.from(document.querySelectorAll("#s-inv-depot-detail button")).find(b => b.textContent.trim() === "Save & Close").click()); await sleep(2200);
  const rows = (await j("GET", "/api/masters/inv_depot", null, token)).body;
  check("the depot is saved in PostgreSQL with its details", rows.length === 1 && rows[0].code === "CHN-" + tag && rows[0].data.city === "Chennai" && rows[0].data.total_capacity_teu === "1500" && rows[0].data.depot_operator === vendor.name, JSON.stringify(rows).slice(0, 300));
  check("Save & Close returns to the Depot list showing the new depot", await page.evaluate(tag => { const s = document.getElementById("s-inv-depot"); return s.classList.contains("on") && s.textContent.includes("Chennai Depot " + tag); }, tag));

  // a yard location picks the depot from the depot master
  await page.evaluate(() => go("inv-yard-location-detail")); await sleep(1500);
  const dep = await page.evaluate(() => Array.from(document.querySelectorAll("#s-inv-yard-location-detail .fg")).find(f => /^Depot/.test(f.textContent.trim())).querySelectorAll("option").length);
  check("Yard Location offers the depots entered above", dep >= 2, dep + "");
  check("required fields are enforced (empty code + name is not saved)", await page.evaluate(async () => { Array.from(document.querySelectorAll("#s-inv-yard-location-detail button")).find(b => b.textContent.trim() === "Save").click(); await new Promise(r => setTimeout(r, 600)); return /complete the required fields/i.test(document.getElementById("s-inv-yard-location-detail").textContent); }));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
