// Real Chrome: the cargo dimensions popup on an Air quotation, and on jobs (Air, Sea LCL, FCL, consolidation by house reference), with quotation -> job.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "AR" + tag, name: "Wiz " + tag, admin_email: `ar${tag}@t.test`, admin_username: "ar" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ar" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const put = (job, sec, data) => j("PUT", "/api/jobs/" + job + "/details/" + sec, { data }, token);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const setup = async (mode, dir, cm) => {
    const n = (await j("POST", "/api/shipments", { mode, direction: dir, container_mode: cm, pol: mode === "Air" ? "MAA" : "INMAA(CHENNAI)", pod: mode === "Air" ? "SIN" : "SGSIN(SINGAPORE)", etd: "2030-05-01", eta: "2030-05-20" }, token)).body.job_number;
    await put(n, "parties", { shipper: "ABC Trading Co", consignee: "Global Importers Ltd", notify1: "Global Importers Ltd", deliveryAgent: "Agent One", originAgent: "Agent Two" });
    await put(n, "cargo", { pkgs: "10", pkgType: "Cartons", gwt: "1000", nwt: "950", vol: "5", cargoDesc: "Machinery parts", marks: "ABC 1-10" });
    if (mode === "Sea") await put(n, "containers", [{ container_no: "TCLU1234567", type: "40HC", seal_no: "S1", pkgs: "10", gross_wt: "1000", net_wt: "950" }]);
    await put(n, "bl", { bookingType: "House", hblType: mode === "Sea" ? "MTO" : undefined, hblNo: dir === "Import" ? "HBL-IMP-1" : undefined, mblNo: "MBL-1", mblDate: "2030-05-01", hblDate: "2030-05-01" });
    await j("POST", "/api/jobs/" + n + "/routing/legs", { carrier: mode === "Air" ? "Emirates" : "MSC Orion", voyage: mode === "Air" ? "EK404" : "VOY1", pol: "Chennai", pod: "Singapore", etd: "2030-05-01", eta: "2030-05-10", stage: "CONFIRMED" }, token);
    if (mode === "Air") await j("POST", "/api/jobs/" + n + "/routing/legs", { carrier: "Emirates", voyage: "EK237", etd: "2030-05-02", eta: "2030-05-11", stage: "PLANNED" }, token);
    if (dir === "Export") { const g = await j("POST", "/api/jobs/" + n + "/hbl", { hbl_type: mode === "Air" ? "HAWB" : "MTO" }, token); }
    return n;
  };
  const results = [];
  for (const [mode, dir, cm] of [["Air", "Export", "Air"], ["Air", "Import", "Air"], ["Sea", "Export", "FCL"], ["Sea", "Import", "FCL"]]) {
    const n = await setup(mode, dir, cm);
    await page.evaluate(n => go("job-detail", { jobNo: n }), n); await sleep(3500);
    await page.evaluate(() => document.querySelector('#s-job-detail .tbb[data-tab="jt7b"]').click()); await sleep(2200);
    const codes = await page.evaluate(() => [...document.querySelectorAll('#odDyn [data-od2="gen"]')].map(b => b.dataset.code));
    for (const code of codes) {
      await page.evaluate(() => { try { closeModal(); } catch (e) {} }); await sleep(300);
      await page.evaluate(c => document.querySelector('#odDyn [data-od2="gen"][data-code="' + c + '"]').click(), code); await sleep(4200);
      let r = { code: mode + " " + dir + " " + code, stage: "" };
      for (let step = 0; step < 3; step++) {
        const state = await page.evaluate(() => ({ go: !!document.getElementById("odInGo"), can: !!document.getElementById("odCanGo"), miss: /Some information is missing/.test((document.getElementById("modalBody") || {}).innerText || ""), pv: !!document.querySelector("#modalBody .pg, #modalBody .bl-sheet") }));
        if (state.pv) { r.stage = "preview"; break; }
        if (state.miss) { r.stage = "missing: " + (await text("#modalBody")).replace(/\s+/g, " ").slice(60, 200); break; }
        if (state.go || state.can) {
          await page.evaluate(() => { document.querySelectorAll("[data-od-in]").forEach(e => { if (e.value) return; if (e.type === "date") e.value = "2030-05-05"; else if (e.type === "number") e.value = "5"; else if (e.tagName === "TEXTAREA") e.value = "ok"; else e.value = "Test"; e.dispatchEvent(new Event("input", { bubbles: true })); }); (document.getElementById("odInGo") || document.getElementById("odCanGo")).click(); });
          await sleep(4500); continue;
        }
        r.stage = "no dialog/preview: " + (await text("#modalBody")).slice(0, 80); break;
      }
      if (r.stage === "preview") { const pv = await text("#modalBody"); r.len = pv.length; r.pages = await page.evaluate(() => document.querySelectorAll("#modalBody .pg, #modalBody .bl-sheet").length); }
      results.push(r); console.log(JSON.stringify(r));
    }
  }
  console.log("ERR", errs.length, errs.join(" | ").slice(0, 400));
  await b.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(2); });
