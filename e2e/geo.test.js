// Headless UI check: State / Province and City follow the country (India: state list + that state's cities; overseas: typed by hand).
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
  const org = await j("POST", "/api/organizations", { code: "GE" + tag.slice(-5).toUpperCase(), name: "Geo " + tag, admin_email: `ge${tag}@t.test`, admin_username: "ge" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ge" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ge" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s);
  await sleep(3000);
  const ev = (el, t) => el.dispatchEvent(new w.Event(t, { bubbles: true }));
  const field = label => { const fg = Array.from(d.querySelectorAll("#s-org-new .fg")).find(x => { const l = x.querySelector("label.fl"); return l && l.textContent.replace(/\*/g, "").trim().toLowerCase().startsWith(label.toLowerCase()); }); return fg && fg.querySelector("input,select,textarea"); };
  const set = (label, v) => { const el = field(label); el.value = v; ev(el, "input"); };
  const cityList = () => Array.from(d.querySelectorAll("#orgCityList option")).map(o => o.value);
  w.go("org-new"); await sleep(1000);
  const country = q("#org_country");
  check("India: the Address tab offers the Indian state list and no typed box", q("#org_addr_state").style.display !== "none" && q("#org_addr_state_text").style.display === "none" && q("#org_addr_state").options.length > 30);
  q("#org_addr_state").value = "Tamil Nadu"; ev(q("#org_addr_state"), "change"); await sleep(100);
  check("choosing Tamil Nadu offers its cities (Chennai, Coimbatore...) for the City box", cityList().includes("Chennai") && cityList().includes("Coimbatore") && !cityList().includes("Mumbai"), cityList().join());
  q("#org_addr_state").value = "Maharashtra"; ev(q("#org_addr_state"), "change"); await sleep(100);
  check("changing the state changes the cities", cityList().includes("Mumbai") && !cityList().includes("Chennai"));
  check("the City box is still free text (a city not in the list can be typed)", q("#org_addr_city").getAttribute("list") === "orgCityList" && q("#org_addr_city").tagName === "INPUT");
  country.value = Array.from(country.options).find(o => /Switzerland/.test(o.text)).value; ev(country, "change"); await sleep(300);
  check("Switzerland: the Indian state list is replaced by a box to type the State / Province, and city suggestions are gone", q("#org_addr_state").style.display === "none" && q("#org_addr_state_text").style.display !== "none" && cityList().length === 0);
  const t = q("#org_addr_state_text"); t.value = "Canton of Geneva"; ev(t, "input");
  set("Org Code", "GEOCH" + tag.slice(-3)); set("Full Legal Name", "Geneva Shipping " + tag); set("Short Name", "GenShip");
  set("Address Line 1", "12 Rue du Rhone"); q("#org_addr_city").value = "Geneva"; ev(q("#org_addr_city"), "input");
  q('#orgRoleFlags .fi[data-role="consignee"]').click(); await sleep(100);
  q("#orgSaveBtn").click(); await sleep(2000);
  const rec = (await j("GET", "/api/customers", null, token)).body.find(c => c.code.toUpperCase() === ("GEOCH" + tag.slice(-3)).toUpperCase());
  check("saved with the typed state and city", !!rec && rec.data.state_province === "Canton of Geneva" && rec.data.city === "Geneva" && rec.country === "Switzerland", JSON.stringify(rec && [rec.country, rec.data]));
  w.go("org-list"); await sleep(800);
  const edit = d.querySelector(`[data-oe="${rec.id}"]`); edit.click(); await sleep(1500);
  check("reopened: the typed State / Province comes back in the box", q("#org_addr_state_text").style.display !== "none" && q("#org_addr_state_text").value === "Canton of Geneva", q("#org_addr_state_text").value);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
