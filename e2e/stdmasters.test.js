// Headless UI check: Container Type / Unit / Pack / Incoterm masters show the standard records, columns, filters and feed the drop-downs.
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
  const org = await j("POST", "/api/organizations", { code: "SM" + tag.slice(-6).toUpperCase(), name: "Std " + tag, admin_email: `sm${tag}@t.test`, admin_username: "sm" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "S" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sm" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const counts = {};
  for (const t of ["container_type", "unit", "pack", "incoterm"]) counts[t] = (await j("GET", `/api/masters/${t}?limit=1000`, null, token)).body.length;
  check("a new company gets the standard records", counts.container_type === 18 && counts.incoterm === 11 && counts.unit > 25 && counts.pack > 20, JSON.stringify(counts));
  const reseed = await j("POST", "/api/masters/standard/seed", null, token);
  check("loading the standard data again creates nothing", reseed.status === 201 && Object.values(reseed.body).every(v => v.created === 0), JSON.stringify(reseed.body));
  const cif = (await j("GET", "/api/masters/incoterm?q=CIF", null, token)).body.find(r => r.code === "CIF");
  check("CIF is sea-only, seller insures and pays freight", cif.data.air_freight === "Not Applicable" && cif.data.insurance === "Seller" && cif.data.freight_payable === "Seller", JSON.stringify(cif.data));
  check("a standard record cannot be deleted", (await j("DELETE", `/api/masters/incoterm/${cif.id}`, null, token)).status === 409);
  check("a standard record's code cannot change", (await j("PATCH", `/api/masters/incoterm/${cif.id}`, { code: "XYZ" }, token)).status === 409);
  const patched = await j("PATCH", `/api/masters/incoterm/${cif.id}`, { status: "INACTIVE", data: { description: "edited" } }, token);
  check("it can be deactivated and edited, and stays a system record", patched.status === 200 && patched.body.status === "INACTIVE" && patched.body.data.system_default === "yes", JSON.stringify(patched.body));
  await j("PATCH", `/api/masters/incoterm/${cif.id}`, { status: "ACTIVE" }, token);
  const custom = await j("POST", "/api/masters/pack", { code: "XPK", name: "Custom Pack", data: {} }, token);
  check("own records can be created and deleted", custom.status === 201 && (await j("DELETE", `/api/masters/pack/${custom.body.id}`, null, token)).status === 204);

  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };

  w.go("container-master"); await sleep(1800);
  check("container cards show type, ISO code and dimensions", d.querySelectorAll("#s-container-master .ctn-card").length === 18 && /ISO 6346: 45G1/.test(text("#s-container-master")) && /payload/.test(text("#s-container-master")));
  check("category pills carry real counts", /Dry \(4\)/.test(text("#s-container-master [data-cat=dry]")) && /Reefer \(3\)/.test(text("#s-container-master [data-cat=reefer]")) && /All \(18\)/.test(text("#s-container-master [data-cat=all]")));
  q("#s-container-master [data-cat=reefer]").click(); await sleep(300);
  check("the Reefer pill filters the cards", d.querySelectorAll("#s-container-master .ctn-card").length === 3 && /Showing 3 of 18/.test(text("#ctnCount")), text("#ctnCount"));
  q("#s-container-master [data-cat=all]").click(); await sleep(200);
  setf(q("#s-container-master input[placeholder*=earch]"), "40HC"); await sleep(300);
  check("search narrows the cards", d.querySelectorAll("#s-container-master .ctn-card").length === 1);
  setf(q("#s-container-master input[placeholder*=earch]"), ""); await sleep(200);

  w.go("unit-master"); await sleep(1500);
  check("units sit in their category tabs with conversion factors", /Kilogram/.test(text("#um1 tbody")) && /Cubic Metre/.test(text("#um2 tbody")) && /Square Metre/.test(text("#um4 tbody")) && /1000/.test(text("#um1 tbody")), text("#um1 tbody").slice(0, 120));
  w.go("pack-master"); await sleep(1500);
  check("packs list the UN/ECE code", /CartonCT/.test(text("#s-pack-master tbody")));
  w.go("incoterms-master"); await sleep(1500);
  const rows = d.querySelectorAll("#s-incoterms-master tbody tr");
  check("11 Incoterms with risk, insurance, clearance and freight columns", rows.length === 11 && /On board the vessel/.test(text("#s-incoterms-master tbody")) && /Seller/.test(text("#s-incoterms-master tbody")), String(rows.length));
  check("system rows have no Delete button", !/Delete/.test(text("#s-incoterms-master tbody")));

  // open the CIF record
  d.querySelector('#s-incoterms-master [data-mk-edit]').click(); await sleep(1500);
  check("the Incoterm detail shows the saved responsibility fields", d.querySelectorAll("#incoAuto > div").length === 6, text("#incoAuto"));

  // drop-downs elsewhere come from the masters
  w.go("crm-enquiry"); await sleep(1200); w.crmRenderEnquiryForm({}); await sleep(1500);
  const opts = id => Array.from(d.querySelectorAll("#" + id + " option")).map(o => o.value);
  check("CRM enquiry Incoterm list is the master (DPU, FAS present)", opts("crmEq_incoterm").includes("DPU") && opts("crmEq_incoterm").includes("FAS"), opts("crmEq_incoterm").join(","));
  check("CRM enquiry container types come from the master", opts("crmEq_conttype").includes("40HC") && opts("crmEq_conttype").includes("20RF"), opts("crmEq_conttype").join(","));

  // container detail: payload is calculated, temperature only for reefers
  w.go("container-master"); await sleep(1200);
  d.querySelector('#s-container-master .ctn-card[data-mk-edit]').click(); await sleep(1500);
  const lab = t => Array.from(d.querySelectorAll("#s-container-detail .fg")).find(g => (g.querySelector("label") || {}).textContent.replace("*", "").trim().startsWith(t)).querySelector("input,select");
  const tmin = lab("Temperature Range Min");
  check("a dry container keeps the reefer temperature fields disabled", tmin.disabled === true);
  setf(lab("Category"), "Reefer", "change"); await sleep(300);
  check("choosing Reefer enables temperature, genset and ventilation", tmin.disabled === false && lab("Genset Required").disabled === false);
  setf(lab("Maximum Gross Weight"), "34000"); setf(lab("Tare Weight"), "4800"); await sleep(300);
  check("payload is gross minus tare", lab("Payload").value === "29200", lab("Payload").value);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
