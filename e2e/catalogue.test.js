// Headless UI check #11: standard freight charge catalogue import and tax verification before activation.
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
  const org = await j("POST", "/api/organizations", { code: "CT" + tag.slice(-6).toUpperCase(), name: "Cat " + tag, admin_email: `ct${tag}@t.test`, admin_username: "ct" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Cat" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ct" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };

  w.go("charge-master"); await sleep(1200);
  q("#importChargesBtn").click(); await sleep(2500);
  const rows = d.querySelectorAll("#s-charge-master tbody tr");
  check("47 standard charges appear in the Charge Master", rows.length === 47, rows.length);
  check("imported charges are inactive and not offered for selection", (await j("GET", "/api/masters/charge?status=ACTIVE", null, token)).body.length === 0);
  const thc = (await j("GET", "/api/masters/charge", null, token)).body.find(c => c.code === "THC");
  w.MK.configs.charge.edit(thc); await sleep(1200);
  check("edit form shows the proposed default, GST % editable and verification fields", /Review required/.test(q("#modalBody").textContent) && !q("#nc_gst").readOnly && !!q("#nc_notif") && !!q("#nc_eff"));
  setf(q("#nc_status"), "Active", "change");
  d.querySelectorAll("#toastStack .toast").forEach(x => x.remove()); q("#nc_save").click(); await sleep(1000);
  const mid = (await j("GET", "/api/masters/charge", null, token)).body.find(c => c.code === "THC");
  check("activation no longer needs a notification reference: the charge becomes Active, still flagged for review", mid.status === "ACTIVE" && mid.data.tax_status === "REVIEW_REQUIRED" && toastsNow().some(t => /Charge saved/i.test(t)), toastsNow().join(";") + " | " + mid.status);
  w.MK.configs.charge.edit(mid); await sleep(1200);
  setf(q("#nc_notif"), "Notification No. 11/2017-CT(R)"); setf(q("#nc_eff"), "2026-04-01"); setf(q("#nc_gst"), "18");
  q("#nc_save").click(); await sleep(1500);
  const after = (await j("GET", "/api/masters/charge", null, token)).body.find(c => c.code === "THC");
  check("recording the notification later verifies the charge and keeps the record", after.status === "ACTIVE" && after.data.notification_ref && after.data.tax_status === "VERIFIED" && after.data.catalogue === "yes", JSON.stringify(after));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
