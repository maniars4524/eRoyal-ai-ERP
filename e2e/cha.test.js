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
  const org = await j("POST", "/api/organizations", { code: "CH" + tag.slice(-6).toUpperCase(), name: "Cha " + tag, admin_email: `ch${tag}@t.test`, admin_username: "ch" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ch" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  w.go("svc-cha"); await sleep(2000);
  const root = d.getElementById("svcChaRoot");
  check("the CHA screen has Back and a Create Job button", !!root.querySelector('[data-go="dashboard"]') && /Create Job/.test(root.textContent), root.textContent.slice(0, 200));
  for (const n of ["Draft", "Pending", "Filed", "Delayed", "Completed", "Examination", "Cleared"]) check("dashboard card " + n, !!Array.from(root.querySelectorAll("[data-cha-group]")).find(b => b.textContent.includes(n)));
  const scr = d.getElementById("s-svc-cha"); check("the old Total customs jobs / Draft / Confirmed / On transit / Closed strip is gone", !/TOTAL CUSTOMS JOBS|ON TRANSIT|CONFIRMED/i.test(scr.textContent), scr.textContent.slice(0, 160));
  check("the four Sea / Air x Export / Import tiles", root.querySelectorAll("[data-cha-type]").length === 4);
  d.getElementById("chaCreate").click(); await sleep(200);
  const opts = Array.from(root.querySelectorAll("[data-cha-new]")).map(b => b.dataset.chaNew);
  check("Create Job offers Export / Import for Sea and Air", ["Export|Sea", "Export|Air", "Import|Sea", "Import|Air"].every(x => opts.includes(x)), opts.join());
  root.querySelector('[data-cha-new="Import|Air"]').click(); await sleep(400);
  const set = (id, v) => { d.getElementById(id).value = v; };
  check("the Air form has no container field", !d.getElementById("chaNCont") && !!d.getElementById("chaNHbl"));
  set("chaNExp", "Berlin GmbH"); set("chaNImp", "Chennai Traders"); set("chaNPol", "DEFRA"); set("chaNPod", "INMAA"); set("chaNEta", "2020-01-05"); set("chaNHbl", "HAWB900");
  d.getElementById("chaNSave").click(); await sleep(2500);
  const list = (await j("GET", "/api/cha/jobs?q=HAWB900", null, token)).body;
  check("the job was created as an Air Import CHA job at CREATED", list.total === 1 && list.items[0].customs_status === "CREATED" && list.items[0].shipment_mode === "Air" && list.items[0].shipment_type === "Import");
  const no = list.items[0].cha_job_number;
  check("the register lists it", root.textContent.includes(no));
  const card = n => Number(Array.from(root.querySelectorAll("[data-cha-group]")).find(b => b.textContent.includes(n)).querySelector("div:nth-child(2)").textContent);
  check("Draft and Delayed count it", card("Draft") === 1 && card("Delayed") === 1, card("Draft") + "/" + card("Delayed"));
  root.querySelector('[data-cha-group="delayed"]').click(); await sleep(1200);
  check("the Delayed card filters the register", root.querySelectorAll("tbody tr [data-cha-open]").length === 1);
  root.querySelector(`[data-cha-open="${no}"]`).click(); await sleep(1500);
  const jobScr = () => d.getElementById("chaJobRoot");
  check("the CHA job opens as a full screen titled like the filing software, with the toolbar and the tabs", /Bill of Entry Filing — /.test(jobScr().textContent) && ["Save", "Validate", "Checklist", "Back to Job", "Duplicate Job"].every(b => Array.from(jobScr().querySelectorAll("button")).some(x => x.textContent.trim() === b)) && ["Job", "General", "IGM", "Invoice", "Product"].every(tb => Array.from(jobScr().querySelectorAll("[data-chj-tab]")).some(x => x.textContent.includes(tb))), jobScr().textContent.slice(0, 160));
  check("the Job tab shows the stages and the import documents", jobScr().querySelectorAll("[data-chj-stage]").length === 11 && /Cargo Arrival Notice/.test(jobScr().textContent) && /Delivery Order/.test(jobScr().textContent));
  jobScr().querySelector('[data-chj-stage="CUSTOMS FILED"]').click(); await sleep(1800);
  check("moving to CUSTOMS FILED is saved", (await j("GET", "/api/cha/jobs/" + encodeURIComponent(no), null, token)).body.customs_status === "CUSTOMS FILED");
  jobScr().querySelector('[data-chj-stage="DOCUMENTATION"]').click(); await sleep(1800);
  check("moving back uses the reason", (await j("GET", "/api/cha/jobs/" + encodeURIComponent(no), null, token)).body.customs_status === "DOCUMENTATION");
  w.closeModal();
  // a CHA job starts from a booking
  const bk = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "Booked Exporter", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "CHA, Transport", pcs: "10", consignee: "Booked Importer" } }, token)).body;
  const bkDone = (await confirmJobs(bk.id, token)).body;
  const plain = (await j("POST", "/api/bookings", { mode: "Sea", direction: "Export", shipment_type: "FCL", booking_type: "Direct", party_name: "No CHA", carrier: "MSC", pol: "INMAA", pod: "BEANR", data: { services: "Freight" } }, token)).body;
  await confirmJobs(plain.id, token);
  d.getElementById("chaCreate").click(); await sleep(200);
  root.querySelector('[data-cha-new="Export|Sea"]').click(); await sleep(1500);
  const opts2 = Array.from(d.querySelectorAll("#chaNBk option")).map(o => o.value);
  check("Create Job asks for the booking first and lists CHA bookings (CHA + Transport) but not a freight-only booking", opts2.includes(bkDone.number) && !opts2.some(v => v && v.includes(plain.number)) && !opts2.includes(plain.number), opts2.join());
  const sel = d.getElementById("chaNBk"); sel.value = bkDone.number; sel.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(300);
  check("the booking's details appear in the form", d.getElementById("chaNExp").value === "Booked Exporter" && d.getElementById("chaNImp").value === "Booked Importer" && d.getElementById("chaNPol").value === "INMAA" && /CHA \+ Transport/.test(d.getElementById("chaNBkInfo").textContent));
  d.getElementById("chaNSave").click(); await sleep(2500);
  const mine = (await j("GET", "/api/cha/jobs?q=BEANR&limit=500", null, token)).body.items.filter(x => x.booking_number === bkDone.number);
  check("a second CHA job is created under the same booking number", mine.length === 2, mine.length);
  // shipment details (mode, direction) are asked inside the form too; ports load from the Port master
  d.getElementById("chaCreate").click(); await sleep(200);
  root.querySelector('[data-cha-new="Export|Sea"]').click(); await sleep(1500);
  check("the form asks Transport mode and Direction first", !!d.querySelector('[data-cha-pick="mode|Sea"]') && !!d.querySelector('[data-cha-pick="mode|Air"]') && !!d.querySelector('[data-cha-pick="dir|Import"]'));
  check("ports are loaded from the Port master for POL / POD", d.querySelectorAll("#chaPorts option").length > 0 && d.getElementById("chaNPol").getAttribute("list") === "chaPorts" && d.getElementById("chaNPod").getAttribute("list") === "chaPorts", d.querySelectorAll("#chaPorts option").length);
  d.querySelector('[data-cha-pick="mode|Air"]').click(); await sleep(1200);
  check("switching to Air re-opens the form for Air (no container field)", /Air Export/.test(d.getElementById("modalTitle").textContent) && !d.getElementById("chaNCont"), d.getElementById("modalTitle").textContent);
  d.querySelector('[data-cha-pick="dir|Import"]').click(); await sleep(1200);
  check("switching to Import re-opens the form for Air Import", /Air Import/.test(d.getElementById("modalTitle").textContent));
  w.closeModal();
  root.querySelector("#chaFClear").click(); await sleep(900);
  check("the register shows the booking number of the jobs", root.textContent.includes(bkDone.number));
  const direct = (await j("GET", "/api/cha/jobs?q=HAWB900", null, token)).body.items[0];
  check("the direct CHA job got a system generated booking number", /^BKG\//.test(direct.booking_number || ""), direct.booking_number);
  w.go("svc-cha"); await sleep(1800);
  root.querySelector(`[data-cha-open="${mine[0].cha_job_number}"]`).click(); await sleep(1800);
  check("the job screen shows the source booking and the order of work", jobScr().textContent.includes(bkDone.number) && /CHA first, then freight/.test(jobScr().textContent));
  // the General tab of the Bill of Entry / Shipping Bill filing form: fill, validate, save
  jobScr().querySelector('[data-chj-tab="General"]').click(); await sleep(400);
  check("General tab has the filing sections and the field check bar", /Fields Checked/.test(jobScr().textContent) && /EXPORTER ADDRESS/i.test(jobScr().textContent) && !!jobScr().querySelector('[data-ff="gstn_type"]') && !!jobScr().querySelector('[data-ff="exporter_class"]'));
  const put = (k, v) => { const e = jobScr().querySelector(`[data-ff="${k}"]`); e.value = v; };
  put("iec", "ABCDE1234H"); put("branch_sr_no", "0 - INMAA1"); put("addr1", "1 Main Road"); put("gstn_type", "GSN"); put("gstn_id", "33ABCDE1234H1Z5"); put("exporter_class", "P"); put("ad_code", "1234567"); put("cons_addr1", "Dock Road 1");
  Array.from(jobScr().querySelectorAll("button")).find(x => x.textContent.trim() === "Validate").click(); await sleep(2500);
  check("after filling the required fields the bar says Ready to file", /Ready to file/.test(jobScr().textContent), jobScr().textContent.slice(0, 200));
  check("Flat File / JSON / Sign buttons are shown but off until customs filing is connected", Array.from(jobScr().querySelectorAll("button[disabled]")).map(x => x.textContent.trim()).includes("Flat File"));
  // documents: entering the BL number on the CHA job makes the document ready
  jobScr().querySelector('[data-chj-tab="Job"]').click(); await sleep(400);
  check("Documents card says the BL is not ready", /Bill of Lading \(BL\) — not ready/.test(jobScr().textContent));
  jobScr().querySelector("#chjHbl").value = "HBL7788"; Array.from(jobScr().querySelectorAll("button")).find(x => x.textContent.trim() === "Save").click(); await sleep(2500);
  check("after saving the HBL number the document is ready", !/not ready/.test(jobScr().textContent) && /Bill of Lading/.test(jobScr().textContent), jobScr().textContent.slice(0, 200));
  Array.from(jobScr().querySelectorAll("button")).find(x => x.textContent.trim() === "Back to Job").click(); await sleep(1500);
  check("Back to Job returns to the CHA list", d.getElementById("s-svc-cha").classList.contains("on"));
  root.querySelector("#chaFClear").click(); await sleep(800);
  root.querySelector('[data-cha-type="Export|Sea"]').click(); await sleep(1000);
  check("the Sea Export tile filters the register", !root.textContent.includes(no));
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

async function confirmJobs(id, token, jobs = ["Freight", "CHA"]) {
  const c = await j("POST", `/api/bookings/${id}/confirm`, null, token); if (c.status !== 200) return c;
  const services = String((c.body.data || {}).services || "Freight").split(",").map(s => s.trim()); let body = c.body; const created = [];
  for (const s of jobs) if (services.includes(s)) { const r = await j("POST", `/api/bookings/${id}/jobs`, { service: s }, token); created.push(r.body.created_job); body = r.body; }
  body.created_jobs = created; return { status: 200, body };
}
