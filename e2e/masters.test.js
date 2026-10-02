// Headless UI check: loads the real page in jsdom against the real API (QA database) and exercises the master screens.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
};
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "UI" + tag.slice(-6).toUpperCase(), name: "UI Test " + tag, admin_email: `ui${tag}@t.test`, admin_username: "ui" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ui" }, dev.body.access_token);
  check("org created via developer API", org.status === 201, JSON.stringify(org.body));
  const lic = org.body.organization.license_number;
  const login = await j("POST", "/api/auth/login", { identifier: "ui" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: lic });
  check("tenant admin logs in on ERP portal", login.status === 200);
  const token = login.body.access_token;

  const dom = await JSDOM.fromURL(BASE + "/erp", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).href, o);
      w.sessionStorage.setItem("esway_access_token", token);
      w.confirm = () => true;
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.matchMedia = w.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
      w.console.error = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await sleep(2500);
  check("page loaded with go() and MK defined", typeof w.go === "function" && w.MK && Object.keys(w.MK.configs).length === 10, Object.keys((w.MK || {}).configs || {}).length);

  const toasts = [];
  const origToast = w.toast; w.toast = (a, b) => { toasts.push(a + " | " + b); try { origToast && origToast(a, b); } catch (e) {} };
  const field = (scr, label) => { const fg = Array.from(d.querySelectorAll("#s-" + scr + " .fg")).find(x => { const l = x.querySelector("label.fl"); return l && l.textContent.replace(/\*/g, "").trim().toLowerCase().startsWith(label.toLowerCase()); }); return fg && fg.querySelector("input,select,textarea"); };
  const rowsText = sel => Array.from(d.querySelectorAll(sel + " tr")).map(r => r.textContent.replace(/\s+/g, " ").trim());

  // ---- Currency: create via the real form
  w.go("currency-master"); await sleep(600);
  check("currency list starts empty (no dummy rows)", rowsText("#cm1 tbody").length === 1 && /No records yet/.test(rowsText("#cm1 tbody")[0]), rowsText("#cm1 tbody").join("|"));
  w.go("currency-detail"); await sleep(400);
  check("new currency form has no dummy values", field("currency-detail", "Currency Code").value === "" && field("currency-detail", "Currency Name").value === "");
  field("currency-detail", "Currency Code").value = "usd"; field("currency-detail", "Currency Name").value = "US Dollar"; field("currency-detail", "Currency Symbol").value = "$";
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(1200);
  const saved = await j("GET", "/api/masters/currency", null, token);
  check("currency persisted in PostgreSQL via UI save", saved.body.length === 1 && saved.body[0].code === "USD" && saved.body[0].data.currency_symbol === "$", JSON.stringify(saved.body) + toasts.join(";"));
  check("list shows the saved currency", rowsText("#cm1 tbody").some(t => t.includes("USD") && t.includes("US Dollar")), rowsText("#cm1 tbody").join("|"));

  // ---- required-field validation and duplicate handling
  w.go("currency-detail"); await sleep(300);
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(300);
  check("empty save is blocked client-side", toasts.some(t => /Required fields missing/.test(t)), toasts.join(";"));
  field("currency-detail", "Currency Code").value = "USD"; field("currency-detail", "Currency Name").value = "Dup";
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(900);
  check("duplicate code reported from server", toasts.some(t => /already exists/.test(t)), toasts.join(";"));

  // ---- Edit flow
  w.go("currency-master"); await sleep(500);
  w.MK.edit("currency", w.MK.cache.currency[0].id); await sleep(500);
  check("edit form is filled from the database", field("currency-detail", "Currency Name").value === "US Dollar" && field("currency-detail", "Currency Symbol").value === "$");
  field("currency-detail", "Currency Name").value = "United States Dollar";
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(1000);
  check("edit persisted", (await j("GET", "/api/masters/currency", null, token)).body[0].name === "United States Dollar");

  // ---- Cross-master connectivity: Branch dropdowns come from Country + Currency masters
  await j("POST", "/api/masters/country", { code: "IN", name: "India", data: { primary_currency: "USD" } }, token);
  w.go("branch-detail"); await sleep(900);
  const countryOpts = Array.from(field("branch-detail", "Country").options).map(o => o.text);
  const ccyOpts = Array.from(field("branch-detail", "Local Currency").options).map(o => o.text);
  check("branch Country dropdown is fed by Country master", countryOpts.includes("India") && !countryOpts.includes("Singapore"), countryOpts.join(","));
  check("branch Local Currency dropdown is fed by Currency master", ccyOpts.some(t => t.startsWith("USD")), ccyOpts.join(","));

  // ---- Unit tabs filter by category; delete flow; other masters render empty not dummy
  await j("POST", "/api/masters/unit", { code: "KG", name: "Kilogram", data: { category: "Weight", decimal_precision: "2" } }, token);
  await j("POST", "/api/masters/unit", { code: "CBM", name: "Cubic Meter", data: { category: "Volume" } }, token);
  w.go("unit-master"); await sleep(700);
  check("unit Weight tab shows only weight units", rowsText("#um1 tbody").length === 1 && rowsText("#um1 tbody")[0].includes("KG") && !rowsText("#um1 tbody")[0].includes("CBM"), rowsText("#um1 tbody").join("|"));
  check("unit Volume tab shows only volume units", rowsText("#um2 tbody").length === 1 && rowsText("#um2 tbody")[0].includes("CBM"));
  const kgId = w.MK.cache.unit.find(u => u.code === "KG").id;
  await w.MK.remove("unit", kgId); await sleep(700);
  check("delete removes the unit from the database", !(await j("GET", "/api/masters/unit", null, token)).body.some(u => u.code === "KG"));
  for (const [list, sel] of [["country-master", "#s-country-master tbody"], ["pack-master", "#s-pack-master tbody"], ["hsn-master", "#s-hsn-master tbody"], ["dept-list", "#s-dept-list tbody"], ["desig-list", "#s-desig-list tbody"]]) {
    w.go(list); await sleep(500);
    const txt = rowsText(sel).join("|");
    check(list + " has no dummy rows", !/Sri Lanka|Kilogram|9965|Operations|Manager/.test(txt), txt.slice(0, 120));
  }
  w.go("country-master"); await sleep(500);
  check("country list shows the saved country", rowsText("#s-country-master tbody").some(t => t.includes("India")), rowsText("#s-country-master tbody").join("|"));

  // ---- isolation: the demo-style second tenant sees nothing
  const other = await j("POST", "/api/organizations", { code: "UB" + tag.slice(-6).toUpperCase(), name: "Other " + tag, admin_email: `ub${tag}@t.test`, admin_username: "ub" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ub" }, dev.body.access_token);
  const l2 = await j("POST", "/api/auth/login", { identifier: "ub" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: other.body.organization.license_number });
  check("second tenant sees none of the first tenant's masters", (await j("GET", "/api/masters/currency", null, l2.body.access_token)).body.length === 0);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
