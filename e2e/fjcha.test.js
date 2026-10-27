// Freight job screen: CUSTOMS / CHA JOB INFORMATION lists all CHA jobs of the booking; Add CHA job validates against the freight job.
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
  const org = await j("POST", "/api/organizations", { code: "FJ" + tag.slice(-6).toUpperCase(), name: "FjCha " + tag, admin_email: `fj${tag}@t.test`, admin_username: "fj" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "fj" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Acme Exports", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "Freight, CHA", pcs: "10", consignee: "Solvay SA" } }, token)).body;
  const done = (await j("POST", `/api/bookings/${bk.id}/confirm`, null, token)).body;
  const fjob = done.jobs.find(x => x.job_type === "Forwarding").job_number;
  w.go("job-detail", { jobNo: fjob }); await sleep(3500);
  d.querySelector('[data-tab="jt3"]').click(); await sleep(1200);
  const card = d.getElementById("chaJobsCard");
  check("the section is shown after the BL section, inside the BL / AWB tab", card.style.display !== "none" && d.getElementById("jt3").contains(card) && /CUSTOMS \/ CHA JOB INFORMATION/.test(card.textContent));
  const heads = Array.from(card.querySelectorAll("th")).map(x => x.textContent);
  check("it has the columns of the CHA job list", ["CHA Job No", "Booking No", "Exporter", "Importer", "POL", "POD", "Shipment Type", "Mode", "SB No / Date", "BE No / Date", "Customs Status", "Documents"].every(h => heads.includes(h)), heads.join());
  check("the booking's CHA job is listed with its status and the BL document", card.querySelectorAll("tbody tr").length === 1 && /CREATED/.test(card.textContent) && /Bill of Lading/.test(card.textContent) && /1/.test(d.getElementById("chaJobsCount").textContent));
  d.getElementById("chaJobsAdd").click(); await sleep(400);
  d.getElementById("fjSave").click(); await sleep(2000);
  check("a second CHA job of the same booking is added and both are listed", card.querySelectorAll("tbody tr").length === 2 && d.getElementById("chaJobsCount").textContent === "2");
  d.getElementById("chaJobsAdd").click(); await sleep(400);
  d.getElementById("fjImp").value = "Another Importer"; d.getElementById("fjPod").value = "DEHAM";
  d.getElementById("fjSave").click(); await sleep(1500);
  const msg = d.getElementById("fjMsg");
  check("a different importer / port is refused with the reason shown", msg.style.display === "block" && /Importer/.test(msg.textContent) && /Port of discharge/.test(msg.textContent), msg.textContent);
  if (d.getElementById("fjWhyWrap").style.display !== "none") {
    d.getElementById("fjWhy").value = "Split consignment"; d.getElementById("fjSave").click(); await sleep(2000);
    check("an authorised user can add it with a reason", card.querySelectorAll("tbody tr").length === 3);
  } else { w.closeModal(); console.log("INFO this user cannot override"); }
  const jobs = (await j("GET", `/api/bookings/${bk.id}/jobs`, null, token)).body;
  check("still one freight job under the booking", jobs.filter(x => x.job_type === "Forwarding").length === 1);
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
