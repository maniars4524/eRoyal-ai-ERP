// Operations menu: Job / Shipment has Create Shipment, Import and Export (Air, Ocean, Road, Multimodal) that open the job list already filtered; the old flat items are gone; Back buttons work.
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
  const org = await j("POST", "/api/organizations", { code: "OM" + tag.slice(-5).toUpperCase(), name: "Opsmenu " + tag, admin_email: `om${tag}@t.test`, admin_username: "om" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Om" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "om" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "SGSIN" }, token);
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Import", container_mode: "FCL", pol: "SGSIN", pod: "INMAA" }, token);
  await j("POST", "/api/shipments", { mode: "Sea", direction: "Export", container_mode: "FCL", pol: "INMAA", pod: "BEANR" }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(6000);
  const rows = Array.from(d.querySelectorAll(".tn-row"));
  const label = r => (r.querySelector(".tn-label") || r).textContent.trim();
  const leaves = rows.filter(r => r.dataset.jobdir !== undefined);
  check("Import and Export each offer Air, Ocean, Road and Multimodal (8 filtered items)", leaves.length === 8 && ["Import", "Export"].every(dir => ["Air Freight", "Ocean Freight", "Road Freight", "Multimodal"].every(l => leaves.some(r => r.dataset.jobdir === dir && label(r) === l))), leaves.map(label).join());
  const jobShip = rows.find(r => label(r) === "Job / Shipment");
  const panel = jobShip && jobShip.closest(".tn-item, .tn-sub, li, div[class*=tn]");
  const text = rows.map(label);
  check("the old flat items are removed (Job Costing, Invoice Generation, Shipment Status, Customs / CHA, Export Shipment, Multimodal Shipment)", !["Job Costing", "Invoice Generation", "Shipment Status", "Customs / CHA", "Export Shipment", "Multimodal Shipment"].some(l => text.includes(l)), text.filter(l => /Costing|Invoice Gen|Shipment Status|Customs \/ CHA|Export Shipment|Multimodal Shipment/.test(l)).join());
  check("Create Shipment is still there", text.includes("Create Shipment"));
  const click = r => { r.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); };
  const vis = () => Array.from(d.querySelectorAll("#jobTableBody tr[data-job-row]")).filter(r => r.style.display !== "none").length;
  click(leaves.find(r => r.dataset.jobdir === "Export" && r.dataset.jobmode === "Air")); await sleep(2500);
  check("Export > Air Freight opens the job list with only the air export job", d.getElementById("s-job-list").classList.contains("on") && d.getElementById("jobModeFilter").value === "Air" && d.getElementById("jobDirFilter").value === "Export" && vis() === 1, vis() + " " + d.getElementById("jobModeFilter").value);
  click(leaves.find(r => r.dataset.jobdir === "Import" && r.dataset.jobmode === "Sea")); await sleep(2500);
  check("Import > Ocean Freight shows only the sea import job", d.getElementById("jobModeFilter").value === "Sea" && d.getElementById("jobDirFilter").value === "Import" && vis() === 1, vis() + "");
  click(leaves.find(r => r.dataset.jobdir === "Export" && r.dataset.jobmode === "")); await sleep(2500);
  check("Export > Multimodal shows every export job (all modes)", d.getElementById("jobModeFilter").value === "" && d.getElementById("jobDirFilter").value === "Export" && vis() === 2, vis() + "");
  // Back buttons that point at "back"
  w.go("container-master"); await sleep(600); w.go("hsn-master"); await sleep(600);
  const back = d.querySelector('#s-hsn-master [data-go="back"]');
  if (back) { back.click(); await sleep(600); }
  check("a Back button on a master screen returns to the page the user came from (not a dead button, not a fixed parent)", !!back && d.querySelector(".scr.on").id === "s-container-master", d.querySelector(".scr.on").id);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
