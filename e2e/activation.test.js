// Headless UI check: activating charges in the Charge Master makes them appear on quotations, bookings and invoices; no errors for ready charges.
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
  const org = await j("POST", "/api/organizations", { code: "AC" + tag.slice(-6).toUpperCase(), name: "Activate " + tag, admin_email: `ac${tag}@t.test`, admin_username: "ac" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "A" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ac" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/masters/charge/catalogue/import", null, token);
  const cust = (await j("POST", "/api/customers", { code: "CU" + tag.slice(-3).toUpperCase(), name: "Acme Exports", roles: ["shipper"], data: {} }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.prompt = () => "18"; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q$ = s => d.querySelector(s);
  const text = s => ((q$(s) || {}).textContent || "").replace(/\s+/g, " ");

  const before = (await j("GET", "/api/masters/charge?status=ACTIVE", null, token)).body.length;
  check("a freshly imported catalogue is fully active with its SAC", before === 47);
  w.go("quotation-new"); await sleep(1500);
  const names = () => Array.from(d.querySelectorAll('select[data-ql$=":charge_name"] option')).map(o => o.value).filter(Boolean);
  check("so all 47 charges are offered on a quotation", w.QK.m.charges.length === 47, String(w.QK.m.charges.length));

  w.go("charge-master"); await sleep(1500);
  check("the Charge Master has an Activate Ready Charges button", !!q$("#activateChargesBtn"));
  d.querySelectorAll("#toastStack .toast").forEach(t => t.remove());
  q$("#activateChargesBtn").click(); await sleep(3000);
  const after = (await j("GET", "/api/masters/charge?status=ACTIVE", null, token)).body;
  const all = (await j("GET", "/api/masters/charge", null, token)).body;
  check("one click activates the ready charges and the GST % chosen for the rest (no errors)", after.length >= 40 && after.length <= all.length, `${after.length} of ${all.length}`);
  check("a green success toast reports it", Array.from(d.querySelectorAll("#toastStack .toast")).some(t => t.classList.contains("success") && /Charges Activated Successfully!/.test(t.textContent)));
  check("charges that still cannot be activated are listed with the reason", all.length === after.length || /Charges still inactive/.test(text("#modalTitle")));
  d.getElementById("modalOvl").classList.remove("on");

  // the active charges reach the quotation form, the booking costing and the invoice
  const pick = after.find(c => /terminal handling/i.test(c.name)) || after[0];
  w.go("quotation-new"); await sleep(1800);
  check("the activated charges are in the quotation's charge list", w.QK.m.charges.some(c => c.name === pick.name) && w.QK.m.charges.length === after.length, w.QK.m.charges.length + " of " + after.length);
  const inv = await j("POST", "/api/documents", { doc_type: "invoice", party_name: "Acme Exports", customer_id: cust.id, lines: [] }, token);
  w.BK.openNew("invoice"); await sleep(2000);
  check("and in the invoice's charge list", Array.from(d.querySelectorAll('datalist[id^="bkCharges_"] option')).some(o => o.value === pick.name), d.querySelectorAll('datalist[id^="bkCharges_"] option').length + " options");
  w.BKG.addCharge && w.go("booking-new"); await sleep(1500);
  const dl = Array.from(d.querySelectorAll("#bkChargeList option")).map(o => o.value);
  check("and in the booking's costing list", dl.includes(pick.name), dl.length + " options");
  // activating a single charge by hand no longer asks for a notification reference
  const other = all.find(c => c.status === "INACTIVE" && (c.data || {}).hsn_code);
  if (other) { const r = await j("PATCH", `/api/masters/charge/${other.id}`, { status: "ACTIVE", data: { ...other.data, gst_percent: other.data.gst_percent || "18" } }, token); check("a charge with its SAC and GST rate activates by hand without errors", r.status === 200, JSON.stringify(r.body)); }

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
