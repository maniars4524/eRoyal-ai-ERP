// Charge Master: manual HSN/SAC entry (when the master has none or lacks the code) and GST % slab picker.
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
  const org = await j("POST", "/api/organizations", { code: "CH" + tag.slice(-6).toUpperCase(), name: "ChHsn " + tag, admin_email: `ch${tag}@t.test`, admin_username: "ch" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ch" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const toastsNow = () => Array.from(d.querySelectorAll("#toastStack .toast")).map(x => x.textContent.replace(/\s+/g, " ").trim());

  w.go("charge-master"); await sleep(1200);
  q("#newChargeBtn").click(); await sleep(1200);
  check("with an empty HSN master the form explains it and opens the manual entry", /No HSN\/SAC codes exist in the master yet/.test(q("#modalBody").textContent) && q("#nc_hsn_man").checked && q("#nc_hsn_manwrap").style.display === "block");
  setf(q("#nc_code"), "THC"); setf(q("#nc_name"), "Terminal Handling");
  d.querySelectorAll("#toastStack .toast").forEach(x => x.remove()); q("#nc_save").click(); await sleep(500);
  check("the code is still mandatory", toastsNow().some(t => /HSN\/SAC required/i.test(t)), toastsNow().join(";"));
  setf(q("#nc_hsn_manual"), "996711"); q("#nc_save").click(); await sleep(500);
  check("the GST % is mandatory too", toastsNow().some(t => /GST % required/i.test(t)), toastsNow().join(";"));
  setf(q("#nc_gst_pick"), "12", "change");
  check("choosing a slab fills the GST % box", q("#nc_gst").value === "12");
  setf(q("#nc_gst"), "7.5");
  check("typing another rate switches the picker to Other", q("#nc_gst_pick").value === "other");
  setf(q("#nc_gst_pick"), "18", "change"); q("#nc_save").click(); await sleep(1800);
  const ch = (await j("GET", "/api/masters/charge", null, token)).body.find(c => c.code === "THC");
  check("charge is saved with the typed HSN/SAC code and the chosen GST %", ch && ch.data.hsn_code === "996711" && ch.data.gst_percent === "18", JSON.stringify(ch));
  const hsn = (await j("GET", "/api/masters/hsn", null, token)).body.find(h => h.code === "996711");
  check("the code was also added to the HSN master with that rate", hsn && hsn.data.gst_rate === "18%" && hsn.data.source === "manual", JSON.stringify(hsn));

  // master now has one code, but another code is still not in it
  w.go("charge-master"); await sleep(1200); q("#newChargeBtn").click(); await sleep(1200);
  check("with codes in the master the normal pick-list is shown first", !q("#nc_hsn_man").checked && /996711/.test(q("#nc_hsn").innerHTML));
  setf(q("#nc_hsn"), "996711", "change");
  check("picking a master code fills its GST %", q("#nc_gst").value === "18" && q("#nc_gst_pick").value === "18");
  q("#nc_hsn_man").click(); await sleep(100);
  check("the manual option is available even when the master has codes", q("#nc_hsn_manwrap").style.display === "block" && q("#nc_hsn").value === "");

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
