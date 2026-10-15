// Headless UI check #15: Currency Master (Currency Information screen, ISO autofill, standard load, base currency, protections).
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
  const org = await j("POST", "/api/organizations", { code: "CU" + tag.slice(-6).toUpperCase(), name: "Cur " + tag, admin_email: `cu${tag}@t.test`, admin_username: "cu" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "cu" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const clearToasts = () => d.querySelectorAll("#toastStack .toast").forEach(x => x.remove());
  const fld = label => { const fg = Array.from(d.querySelectorAll("#s-currency-detail .fg")).find(f => (f.querySelector("label").textContent || "").trim().startsWith(label)); return fg ? fg.querySelector("input,select,textarea") : null; };

  // ---------- standard load
  w.go("currency-master"); await sleep(1500);
  q("#curSeedBtn").click(); await sleep(3500);
  const rows = (await j("GET", "/api/masters/currency", null, token)).body;
  check("Load Standard Currencies adds the 15 sample currencies", rows.length === 15 && /Standard currencies loaded/.test(toastsNow().join(" ")), rows.length + " " + toastsNow().join(";"));
  check("the list shows symbol, numeric code (036 keeps its zero), decimals and the system / base columns", /AUD/.test(text("#cm1")) && /036/.test(text("#cm1")) && /A\$/.test(text("#cm1")) && /Yes — base/.test(text("#cm1")), text("#cm1").slice(0, 200));
  check("an Indian company gets INR as its base currency, and no other currency is base", rows.filter(r => r.data.default_currency === "Yes").map(r => r.code).join() === "INR" && rows.find(r => r.code === "INR").data.system_default === "Yes");
  clearToasts(); q("#curSeedBtn").click(); await sleep(2500);
  check("loading again never duplicates", (await j("GET", "/api/masters/currency", null, token)).body.length === 15 && /0 added, 15 already existed/.test(toastsNow().join(" ")), toastsNow().join(";"));

  // ---------- new currency: ISO lookup fills the form
  q('[data-go="currency-detail"]').click(); await sleep(2200);
  check("screen is titled Currency Information with every field of the form", /Currency Information/.test(text("#s-currency-detail")) && ["Currency Code (ISO 4217)", "Currency Name", "Currency Symbol", "Unicode Symbol", "Symbol Position", "ISO Numeric Code", "Decimal Places", "Sub Currency Name", "Sub Currency Ratio", "Active Status", "Default Currency", "System Default", "Notes"].every(l => !!fld(l)));
  check("the system-currency badge is hidden for a new currency", q("#curSysBadge").style.display === "none");
  setf(fld("Currency Code"), "nok");
  check("typing an ISO code fills name, numeric code and decimals", fld("Currency Code").value === "NOK" && /Krone/.test(fld("Currency Name").value) && fld("ISO Numeric Code").value === "578" && fld("Decimal Places").value === "2", fld("Currency Name").value);
  setf(fld("Currency Symbol"), "kr"); setf(fld("Symbol Position"), "SUFFIX", "change"); setf(fld("Sub Currency Name"), "Ore"); setf(fld("Sub Currency Ratio"), "100");
  check("display sample follows the symbol position and decimals", /1\.234,50 kr/.test(fld("Display sample") ? fld("Display sample").value : q("#cur_sample").value), q("#cur_sample").value);
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(1800);
  const nok = (await j("GET", "/api/masters/currency", null, token)).body.find(r => r.code === "NOK");
  check("saved with the ISO numeric code as text and a suffix position", nok && nok.data.iso_numeric_code === "578" && nok.data.symbol_position === "SUFFIX" && nok.data.default_currency === "No", JSON.stringify(nok && nok.data));

  // ---------- server validation reaches the screen
  w.go("currency-master"); await sleep(1200); q('[data-go="currency-detail"]').click(); await sleep(1800);
  setf(fld("Currency Code"), "ZZZ"); setf(fld("Currency Name"), "Nonsense"); clearToasts();
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(1500);
  check("a code that is not in ISO 4217 is refused", toastsNow().some(t => /not an ISO 4217/.test(t)), toastsNow().join(";"));
  setf(fld("Currency Code"), "SEK"); setf(fld("Currency Name"), "Swedish Krona"); setf(fld("Decimal Places"), "3"); clearToasts();
  d.querySelector("#s-currency-detail .pa .btn-suc").click(); await sleep(1500);
  check("decimals that contradict ISO 4217 are refused", toastsNow().some(t => /2 decimal places/.test(t)), toastsNow().join(";"));

  // ---------- system currency: protected
  const usd = (await j("GET", "/api/masters/currency", null, token)).body.find(r => r.code === "USD");
  w.MK.pending = { type: "currency", id: usd.id, rec: usd }; w.go("currency-detail"); await sleep(2200);
  check("a system currency shows the protected badge and its code cannot be edited", q("#curSysBadge").style.display !== "none" && fld("Currency Code").readOnly === true && /Currency Information — US Dollar/.test(text("#curTitle")), text("#curTitle"));
  check("Default Currency is read-only on the form", q("#cur_default").disabled === true && q("#cur_default").value === "No");
  const del = await j("DELETE", `/api/masters/currency/${usd.id}`, null, token);
  check("deleting a system currency is refused", del.status === 409 && /system currency/.test(del.body.detail));

  // ---------- base currency
  check("Set as base currency is offered to an authorised user", q("#curSetBase").style.display !== "none");
  q("#curSetBase").click(); await sleep(500);
  clearToasts(); q("#curBaseGo").click(); await sleep(900);
  check("the change needs a confirmation and a reason", toastsNow().some(t => /Confirm the change and give a reason/.test(t)), toastsNow().join(";"));
  setf(q("#curBaseWhy"), "Group reporting currency"); q("#curBaseOk").click(); q("#curBaseGo").click(); await sleep(1800);
  const after = (await j("GET", "/api/masters/currency", null, token)).body;
  check("exactly one base currency afterwards", after.filter(r => r.data.default_currency === "Yes").map(r => r.code).join() === "USD");
  check("the previous base currency is no longer base", after.find(r => r.code === "INR").data.default_currency === "No");

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
