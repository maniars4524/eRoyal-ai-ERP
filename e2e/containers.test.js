// Real Chrome: Inventory & Asset Management - dashboard, container register (ISO 6346 hint), movement IN with approval, status change, reversal.
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
  const org = await j("POST", "/api/organizations", { code: "CN" + tag, name: "Cont " + tag, admin_email: `cn${tag}@t.test`, admin_username: "cn" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cn" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const m = (t, code, name, data) => j("POST", "/api/masters/" + t, { code, name, data: data || {} }, token);
  await m("inv_container_size", "S20", "20 FT", { length_feet: "20" }); await m("inv_container_ownership", "OWN", "Company owned", { treatment: "Company fixed asset" }); await m("inv_container_grade", "G1", "Grade 1");
  await m("inv_container_condition", "GOOD", "Good", { available_for_use: "Yes" }); await m("inv_container_condition", "DMG", "Damaged", { available_for_use: "No" });
  await m("inv_depot", "CHN", "Chennai Depot"); await m("inv_yard_location", "A01", "Chennai A01", { depot: "Chennai Depot" });
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 900 }); const errs = []; page.on("pageerror", e => errs.push(e.message)); page.on("dialog", d => d.accept("wrong container"));
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const setv = (sel, val) => page.evaluate((sel, val) => { const e = document.querySelector(sel); e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, val);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);

  const goes = await page.evaluate(() => Array.from(document.querySelectorAll('.tn-row[data-go^="inv-"]')).map(r => r.dataset.go));
  check("the Inventory & Asset Mgmt menu has Dashboard, Container Inventory and Movement Management", ["inv-dashboard", "inv-containers", "inv-movements"].every(x => goes.includes(x)), goes.join());
  await click('.tn-row[data-go="inv-dashboard"]'); await sleep(1500);
  const d0 = await text("#s-inv-dashboard");
  check("the dashboard starts at zero with an honest empty state (no sample figures)", /Total containers\s*0/i.test(d0.replace(/\n/g, " ")) && /No container is at a depot yet/.test(d0), d0.slice(0, 200));

  await click('.tn-row[data-go="inv-containers"]'); await sleep(1500);
  check("the container list starts empty", /No containers registered yet/.test(await text("#s-inv-containers")));
  await click("#invNewCont"); await sleep(1500);
  await setv("#ic_container_number", "CSQU3054384"); await sleep(900);
  check("a wrong check digit is flagged with the right one", /Check digit should be 3/.test(await text("#invNumHint")), await text("#invNumHint"));
  await setv("#ic_container_number", "csqu 3054383"); await sleep(900);
  check("a valid ISO 6346 number is confirmed", /Valid ISO 6346/.test(await text("#invNumHint")));
  await setv("#ic_size", "20 FT"); await setv("#ic_ownership", "Company owned"); await setv("#ic_grade", "Grade 1"); await setv("#ic_condition", "Good"); await setv("#ic_manufacturer", "CIMC");
  await click("#invCsave"); await sleep(2500);
  const rows = (await j("GET", "/api/containers", null, token)).body;
  check("the container is saved in PostgreSQL with clean number and details", rows.total === 1 && rows.items[0].container_number === "CSQU3054383" && rows.items[0].manufacturer === "CIMC" && rows.items[0].is_company_asset === true, JSON.stringify(rows).slice(0, 250));
  check("after saving it opens the container (tabs shown)", /Status & Ownership/.test(await text("#s-inv-container-detail")) && /CSQU3054383/.test(await text("#s-inv-container-detail")));

  await click('.tn-row[data-go="inv-movements"]'); await sleep(1500);
  await click("#invNewMove"); await sleep(1500);
  await setv("#im_cn", "CSQU3054383"); await sleep(1200);
  check("the movement form recognises the registered container", /Not in the register/.test(await text("#im_cnhint")) === false && /20 FT/.test(await text("#im_cnhint")), await text("#im_cnhint"));
  await setv("#im_depot", "Chennai Depot"); await sleep(300); await setv("#im_loc", "A01"); await setv("#im_truck", "TN01AB1234");
  await click("#im_save"); await sleep(2500);
  check("the movement is created as DRAFT and the container is not moved yet", /Draft/.test(await text("#invMbody")) && (await j("GET", "/api/containers/" + rows.items[0].id, null, token)).body.location_status === "NOT_AT_DEPOT");
  await page.evaluate(() => document.querySelector('[data-invmv^="approve:"]').click()); await sleep(2500);
  const c1 = (await j("GET", "/api/containers/" + rows.items[0].id, null, token)).body;
  check("approving places the container at the depot, available, in the yard location", c1.depot === "Chennai Depot" && c1.location_status === "AT_DEPOT" && c1.availability === "AVAILABLE" && c1.yard_location === "A01", JSON.stringify(c1).slice(0, 250));

  await click('.tn-row[data-go="inv-dashboard"]'); await sleep(1800);
  const d1 = (await text("#s-inv-dashboard")).replace(/\n/g, " ");
  check("the dashboard now shows the container in its depot", /Chennai Depot/.test(d1) && /Total containers\s*1/i.test(d1) && /Available\s*1/i.test(d1), d1.slice(0, 300));

  await page.evaluate(id => { window.INVC.openId = id; go("inv-container-detail"); }, rows.items[0].id); await sleep(1800);
  await page.evaluate(() => document.querySelector('[data-invtab="1"]').click());
  await setv("#ic_new_condition", "Damaged"); await setv("#ic_reason", "dent on roof panel"); await click("#invCstatus"); await sleep(2500);
  const c2 = (await j("GET", "/api/containers/" + rows.items[0].id, null, token)).body;
  check("changing the condition to Damaged makes it not available and records why", c2.condition === "Damaged" && c2.availability === "NOT_AVAILABLE" && c2.history.some(h => h.field === "condition" && h.reason === "dent on roof panel"), JSON.stringify(c2.history).slice(0, 250));
  await page.evaluate(() => document.querySelector('[data-invtab="3"]').click());
  check("the Status History tab lists the change", /dent on roof panel/.test(await text("#s-inv-container-detail")));

  await click('.tn-row[data-go="inv-movements"]'); await sleep(1500);
  await page.evaluate(() => document.querySelector('[data-invmv^="reverse:"]').click()); await sleep(2500);
  const c3 = (await j("GET", "/api/containers/" + rows.items[0].id, null, token)).body;
  check("reversing the movement puts the container back as it was before", c3.location_status === "NOT_AT_DEPOT" && c3.depot === null, JSON.stringify(c3).slice(0, 250));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
