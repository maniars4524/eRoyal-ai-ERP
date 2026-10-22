// Headless UI check: direct job, House booking type: Next is enabled once the House BL type (and SCAC code where needed) is chosen.
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
  const org = await j("POST", "/api/organizations", { code: "JW" + tag.slice(-5).toUpperCase(), name: "Jw " + tag, admin_email: `jw${tag}@t.test`, admin_username: "jw" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Jw" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "jw" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document; const q = x => d.querySelector(x);
  await sleep(3000);
  const ev = (el, t) => el.dispatchEvent(new w.Event(t, { bubbles: true }));
  w.go("job-new"); await sleep(1200);
  const pick = async (grid, text) => { const c = Array.from(d.querySelectorAll(grid + " [data-pick]")).find(x => x.textContent.includes(text)); c.click(); await sleep(250); };
  await pick("#jobWizModeGrid", "Sea"); await pick("#jobWizDirGrid", "Export"); await pick("#jobWizTypeGrid", "FCL"); await pick("#jobWizBTGrid", "House");
  const sel = (id, v) => { const e = q("#" + id); e.value = v; ev(e, "change"); };
  
  const nx = () => q("#jobWizNext").disabled;
  check("House needs the House BL type before Next", nx() === true);
  sel("jobWizHbltype", "OTL"); await sleep(200);
  check("OTL asks for the SCAC code and Next waits for it", q("#jobWizScacWrap").style.display === "block" && nx() === true);
  sel("jobWizScac", "RTSU"); await sleep(200);
  check("choosing the SCAC code enables Next", nx() === false, JSON.stringify(w.jobWiz));
  await pick("#jobWizBTGrid", "Direct"); await pick("#jobWizBTGrid", "House");
  check("switching to Direct and back to House clears the House BL type and SCAC (no stale values)", q("#jobWizHbltype").value === "" && q("#jobWizScac").value === "" && q("#jobWizScacWrap").style.display === "none" && nx() === true);
  sel("jobWizHbltype", "OTL"); await sleep(200); sel("jobWizScac", "ARKU"); await sleep(200);
  sel("jobWizHbltype", "FMC"); await sleep(200);
  check("FMC needs no SCAC code: Next is enabled and the old SCAC is cleared", q("#jobWizScacWrap").style.display === "none" && q("#jobWizScac").value === "" && nx() === false, JSON.stringify(w.jobWiz));
  sel("jobWizHbltype", "OTL"); await sleep(200);
  check("back to OTL: the SCAC shows blank and Next waits (no stale RTSU look)", q("#jobWizScac").value === "" && nx() === true);
  sel("jobWizScac", "IGLU"); await sleep(200); q("#jobWizNext").click(); await sleep(800);
  console.log(failures ? "
" + failures + " FAILED" : "
ALL UI CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
