// Organization roles (Exporter / Importer / CHA) open their own master options; the CHA screen picks parties and defaults from them.
// CHA screen: Back + Create Job (Export / Import x Sea / Air), dashboard cards (Draft, Pending, Filed, Delayed, Completed ...), register, job window with stages.
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
  const org = await j("POST", "/api/organizations", { code: "OR" + tag.slice(-6).toUpperCase(), name: "OrgRoles " + tag, admin_email: `or${tag}@t.test`, admin_username: "or" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "or" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const cust = async (code, name, roles, data) => (await j("POST", "/api/customers", { code, name, roles, data, country: "India" }, token));
  const click = async sel => { d.querySelector(sel).click(); await sleep(500); };
  w.go("org-new"); await sleep(1500);
  check("no trade blocks before a role is ticked", !d.getElementById("net_exp_ad_code") && !d.getElementById("net_cha_license_no"));
  await click('#orgRoleFlags .fi[data-role="shipper"]');
  check("Exporter role opens AD code, AD bank, default POL, RODTEP and default CHA", ["net_exp_ad_code", "net_exp_ad_bank", "net_exp_default_pol", "net_exp_rodtep", "net_exp_default_cha"].every(id => d.getElementById(id)));
  await click('#orgRoleFlags .fi[data-role="consignee"]');
  check("Importer role opens default POD, customs location and default CHA", ["net_imp_default_pod", "net_imp_customs_location", "net_imp_default_cha"].every(id => d.getElementById(id)));
  await click('#orgRoleFlags .fi[data-role="cha"]');
  check("CHA role opens licence no, validity, stations and filing mode", ["net_cha_license_no", "net_cha_license_valid_to", "net_cha_customs_stations", "net_cha_filing_mode"].every(id => d.getElementById(id)));
  const put = (id, v) => { d.getElementById(id).value = v; };
  check("with GST Applicable = Yes the IEC Code is in the tax block, not repeated in the exporter block", !d.getElementById("net_iec_code") && !!d.getElementById("org_iec"));
  { const c = d.getElementById("org_country"); c.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300); }
  { const g = d.getElementById("org_gst_applicable"); g.value = "No"; g.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(400); }
  check("with GST Applicable = No the Exporter / Importer block shows the IEC Code", !!d.getElementById("net_iec_code"));
  put("net_iec_code", "0512098765");
  check("the IEC typed there is collected as iec_code", w.OrgExt.collect().iec_code === "0512098765", JSON.stringify(w.OrgExt.collect()).slice(0, 200));
  { const g = d.getElementById("org_gst_applicable"); g.value = "Yes"; g.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(400); }
  check("back to GST Yes the block hides it again", !d.getElementById("net_iec_code"));
  put("net_exp_ad_code", "ad77"); put("net_exp_default_pol", "inmaa"); put("net_cha_license_no", "chn/12"); put("net_cha_filing_mode", "Export and Import");
  check("Exporter / Importer open Unit kind (General, SEZ ...) and approval; Exporter also LUT", ["net_trade_unit_kind", "net_trade_approval_no", "net_exp_lut_no", "net_exp_lut_valid_to"].every(id => d.getElementById(id)) && Array.from(d.getElementById("net_trade_unit_kind").options).map(x => x.text).includes("SEZ"));
  put("net_trade_unit_kind", "SEZ"); put("net_trade_approval_no", "sez/77");
  const got = w.OrgExt.collect();
  check("the values are collected into the organization data", got.trade_unit_kind === "SEZ" && got.trade_approval_no === "SEZ/77" && got.exp_ad_code === "AD77" && got.exp_default_pol === "INMAA" && got.cha_license_no === "CHN/12" && got.cha_filing_mode === "Export and Import", JSON.stringify(got));
  await click('#orgRoleFlags .fi[data-role="shipper"]');
  check("un-ticking a role drops its fields from the data", w.OrgExt.collect().exp_ad_code === undefined && w.OrgExt.collect().cha_license_no === "CHN/12");
  // saved through the API and read back
  const chaOrg = await cust("CHAX1", "Sway Impex CHA", ["cha", "ar"], { cha_license_no: "CHN/12", cha_filing_mode: "Export and Import" });
  const expOrg = await cust("EXPX1", "Madras Exports", ["shipper"], { exp_default_pol: "INMAA", exp_default_cha: "Sway Impex CHA", iec_code: "0512098765", exp_ad_code: "AD1", trade_unit_kind: "SEZ" });
  check("organizations keep the role fields", chaOrg.status === 201 && expOrg.status === 201 && expOrg.body.data.exp_default_cha === "Sway Impex CHA", JSON.stringify(expOrg.body).slice(0, 200));
  const chas = (await j("GET", "/api/customers?role=cha", null, token)).body;
  check("only CHA organizations are listed for the CHA role", chas.length === 1 && chas[0].name === "Sway Impex CHA");
  // CHA screen picks the exporter from the Organization master and fills the default port
  w.go("svc-cha"); await sleep(1500);
  d.getElementById("chaCreate").click(); await sleep(200);
  d.querySelector('[data-cha-new="Export|Sea"]').click(); await sleep(1500);
  const exp = d.getElementById("chaNExp"); exp.value = "Madras Exports"; exp.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("the CHA form lists organizations and fills the default port of loading", !!d.querySelector("#chaOrgExp option[value='Madras Exports']") && d.getElementById("chaNPol").value === "INMAA", d.getElementById("chaNPol").value);
  w.closeModal();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
