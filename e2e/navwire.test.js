// Navigation and wiring walk: booking <-> freight job <-> CHA job <-> CHA list <-> search <-> dashboard slides, with Back at every step.
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
  const org = await j("POST", "/api/organizations", { code: "NW" + tag.slice(-6).toUpperCase(), name: "NavWire " + tag, admin_email: `nw${tag}@t.test`, admin_username: "nw" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "nw" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Nav Exports", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "Freight, CHA", pcs: "10", consignee: "Nav Buyer" } }, token)).body;
  const done = (await j("POST", `/api/bookings/${bk.id}/confirm`, null, token)).body;
  check("confirming created no job", done.jobs.length === 0 && done.created_jobs.length === 0);
  const on = id => d.getElementById(id).classList.contains("on");
  const click = async (sel, ms = 1500) => { const e = d.querySelector(sel); if (!e) throw new Error("missing " + sel); e.click(); await sleep(ms); };
  // booking page: create both jobs from the one booking number
  w.BKG.view(bk.id); await sleep(2500);
  check("booking page offers Create for the freight and the CHA job", !!d.querySelector('[data-bkv-create="Freight"]') && !!d.querySelector('[data-bkv-create="CHA"]'));
  await click('[data-bkv-create="CHA"]', 2500);
  await click('[data-bkv-create="Freight"]', 2500);
  const jobs = (await j("GET", `/api/bookings/${bk.id}/jobs`, null, token)).body;
  const fjob = jobs.find(x => x.job_type === "Forwarding").job_number, cjob = jobs.find(x => x.job_type === "Customs").job_number;
  check("both jobs carry the booking number and the CHA job its own series", /^SE\/CC\/\d{3}/.test(cjob) && /^SEAEXP-/.test(fjob));
  // booking page -> CHA job opens the CHA screen; -> freight job opens the job page
  await click(`[data-bkv-job="${cjob}"]`, 2500);
  check("booking page -> CHA job opens the CHA job screen", on("s-cha-job") && d.getElementById("chaJobRoot").textContent.includes(cjob));
  // CHA job -> booking
  await click("[data-chj-booking]", 2000);
  check("CHA job -> booking opens the booking page", on("s-booking-view"));
  w.navBack(); await sleep(1800);
  check("Back from the booking returns to the CHA job", on("s-cha-job") && d.getElementById("chaJobRoot").textContent.includes(cjob));
  // CHA job -> freight job
  await click("[data-svc-job]", 3000);
  check("CHA job -> freight job opens the freight job page", on("s-job-detail"));
  // freight job -> CHA card -> CHA job
  d.querySelector('[data-tab="jt3"]').click(); await sleep(1500);
  check("the freight job lists its CHA job in CUSTOMS / CHA JOB INFORMATION", d.getElementById("chaJobsBody").textContent.includes(cjob) && d.getElementById("chaJobsBody").textContent.includes(done.number));
  await click(`[data-fjcha-open="${cjob}"]`, 2500);
  check("freight job -> CHA job opens the CHA job screen", on("s-cha-job"));
  w.navBack(); await sleep(1800);
  check("Back returns to the freight job", on("s-job-detail"));
  // CHA list -> job and Back to Job
  w.go("svc-cha"); await sleep(2000);
  check("CHA register shows the booking number and the freight job", d.getElementById("svcChaRoot").textContent.includes(done.number) && d.getElementById("svcChaRoot").textContent.includes(fjob));
  await click(`[data-cha-open="${cjob}"]`, 2500);
  check("register -> CHA job screen", on("s-cha-job"));
  Array.from(d.querySelectorAll("#chaJobRoot button")).find(x => x.textContent.trim() === "Back to Job").click(); await sleep(1800);
  check("Back to Job returns to the CHA register", on("s-svc-cha"));
  // an unsaved filing form is guarded
  await click(`[data-cha-open="${cjob}"]`, 2500);
  d.querySelector('[data-chj-tab="General"]').click(); await sleep(400);
  const f1 = d.querySelector('[data-ff="iec"]'); f1.value = "X1"; f1.dispatchEvent(new w.Event("input", { bubbles: true }));
  let asked = 0; w.confirm = () => { asked++; return false; };
  w.navBack(); await sleep(600);
  check("leaving the filing form with unsaved changes asks first, and staying keeps the screen", asked === 1 && on("s-cha-job"));
  w.confirm = () => true;
  // global search finds the CHA job and opens the CHA screen
  w.go("dashboard"); await sleep(1200);
  const box = d.getElementById("globalSearch") || d.querySelector('input[placeholder^="Search jobs"]');
  box.value = cjob.split("/")[2]; box.dispatchEvent(new w.Event("input", { bubbles: true })); await sleep(1800);
  const hit = Array.from(d.querySelectorAll("[data-gs]")).find(x => x.textContent.includes(cjob));
  check("header search lists the CHA job", !!hit, d.body.textContent.includes(cjob));
  if (hit) { hit.dispatchEvent(new w.MouseEvent("mousedown", { bubbles: true })); await sleep(2500); check("search result opens the CHA job screen", on("s-cha-job")); }
  // dashboard slide tiles go to their module
  w.go("dashboard"); await sleep(1500);
  d.querySelector('.dash-dot[title="CHA (Customs)"]').click(); await sleep(1500);
  d.querySelector('.dash-slide.active [data-go="svc-cha"]').click(); await sleep(1500);
  check("CHA slide tile opens the CHA screen", on("s-svc-cha"));
  // freight module: New job from booking list no longer offers a booking that has its freight job
  w.go("job-new"); await sleep(1800);
  check("New Shipment Job lists only bookings still waiting for a freight job", !Array.from(d.querySelectorAll("#jfbSel option")).some(o => o.textContent.includes(done.number)));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
