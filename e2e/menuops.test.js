// Menu: Operations = All Jobs, Job / Shipment (Create Shipment, Import, Export, Shipment Console); Logistics & Customs = Booking, Shipment-free list (CHA, Transport, Warehouse).
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) }; };
let failures = 0;
const check = (name, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + (extra || ""))); if (!ok) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "MN" + tag.slice(-6).toUpperCase(), name: "Menu " + tag, admin_email: `mn${tag}@t.test`, admin_username: "mn" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "mn" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  const top = label => Array.from(d.querySelectorAll("#topnavMenu > .tn-item")).find(x => x.querySelector(".tn-row .tn-label").textContent.trim() === label);
  const kids = li => Array.from(li.querySelectorAll(":scope > .tn-panel > .tn-item > .tn-row .tn-label, :scope > .tn-panel > * > .tn-row .tn-label")).map(x => x.textContent.trim());
  const ops = top("Operations"), svc = top("Logistics & Customs");
  const opsKids = kids(ops), svcKids = kids(svc);
  check("Operations holds All Jobs and Job / Shipment only", opsKids.join("|") === "All Jobs|Job / Shipment", opsKids.join("|"));
  check("Logistics & Customs holds Booking, CHA, Transport and Warehouse", svcKids.join("|") === "Booking|CHA|Transport|Warehouse", svcKids.join("|"));
  const js = Array.from(ops.querySelectorAll(".tn-item")).find(x => x.querySelector(".tn-row .tn-label").textContent.trim() === "Job / Shipment");
  const jsKids = kids(js);
  check("Job / Shipment has Import, Export and Shipment Console (third, after Export)", jsKids.indexOf("Import") >= 0 && jsKids.indexOf("Export") === jsKids.indexOf("Import") + 1 && jsKids.indexOf("Shipment Console") === jsKids.indexOf("Export") + 1, jsKids.join("|"));
  check("Create Shipment is kept (it is where a freight job is made from a booking)", jsKids.includes("Create Shipment"), jsKids.join("|"));
  const bk = Array.from(svc.querySelectorAll(".tn-item")).find(x => x.querySelector(".tn-row .tn-label").textContent.trim() === "Booking");
  check("Booking under Logistics & Customs keeps Create Booking, Booking Status and Carrier Booking", ["Create Booking", "Booking Status", "Carrier Booking"].every(l => kids(bk).includes(l)), kids(bk).join("|"));
  // Logistics & Customs > CHA > Sea Export / Sea Import / Air Export / Air Import
  const chaNode = Array.from(svc.querySelectorAll(".tn-item")).find(x => x.querySelector(".tn-row .tn-label").textContent.trim() === "CHA");
  const chaKids = kids(chaNode);
  check("CHA has Sea Export, Sea Import, Air Export and Air Import (and the CHA Dashboard)", ["CHA Dashboard", "Sea Export", "Sea Import", "Air Export", "Air Import"].every(l => chaKids.includes(l)), chaKids.join("|"));
  const choose = async label => { const row = Array.from(chaNode.querySelectorAll(".tn-panel .tn-row")).find(r => r.textContent.trim() === label); row.click(); await sleep(2500); return w.ChaKit.state; };
  let st = await choose("Sea Export");
  check("Sea Export opens the CHA register for Export + Sea", st.direction === "Export" && st.mode === "Sea" && d.getElementById("s-svc-cha").classList.contains("on") && /Export/.test(d.getElementById("chaFDir").value), JSON.stringify(st));
  st = await choose("Air Import");
  check("Air Import opens the CHA register for Import + Air", st.direction === "Import" && st.mode === "Air" && d.getElementById("chaFDir").value === "Import" && d.getElementById("chaFMode").value === "Air", JSON.stringify(st));
  st = await choose("CHA Dashboard");
  check("CHA Dashboard clears the filter", st.direction === "" && st.mode === "" && d.getElementById("chaFDir").value === "");
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
