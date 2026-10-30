// Enquiry: View / Edit after it was created, with every container row.
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
  const org = await j("POST", "/api/organizations", { code: "EV" + tag.slice(-6).toUpperCase(), name: "EnqView " + tag, admin_email: `ev${tag}@t.test`, admin_username: "ev" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ev" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  for (const c of ["20GP", "40HC", "20RF"]) await j("POST", "/api/masters/container_type", { code: c, name: c }, token);
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.print = () => {}; w.prompt = () => "wrong HS code"; } });
  const w = dom.window, d = w.document;
  await sleep(6500);

  const enq = { id: "ENQ-TEST-1", company: "Acme Exports", contact: "Raj", shipmentType: "Sea Export (FCL)", department: "Freight", modeOfTransport: "Sea", incoterm: "FOB", origin: "Chennai ICD", pol: "INMAA", pod: "BEANR", destination: "Antwerp CY", finalDelivery: "Brussels", commodity: "Garments", packages: "10", grossWeight: "2000", volumeCBM: "3", containerType: "20GP", containerQty: "2", containers: [{ type: "20GP", qty: "2" }, { type: "40HC", qty: "1" }], status: "New", timeline: [] };
  w.go("crm-enquiry"); await sleep(2500);
  w.CRM_ENQUIRIES.push(enq); w.renderCrmEnquiry(); await sleep(500);
  const open = d.querySelector('[data-crm-enq-open="ENQ-TEST-1"]');
  check("every enquiry row has View / Edit", !!open);
  open.click(); await sleep(1200);
  const modal = () => (d.getElementById("modalBox") || d.querySelector(".modal-box") || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const rows = () => Array.from(d.querySelectorAll("#eqvConts [data-eqv-row]")).map(r => r.querySelector("select").value + "x" + r.querySelector("input").value);
  check("the enquiry opens with its places, cargo and every container row", /Place of Receipt/.test(modal()) && /Chennai ICD/.test(d.querySelector('[data-eqv="origin"]').value) && rows().length === 2 && rows().join() === "20GPx2,40HCx1", rows().join());
  d.getElementById("eqvAdd").click(); await sleep(100);
  const sel = d.querySelectorAll("#eqvConts [data-eqv-row]")[2]; sel.querySelector("select").value = Array.from(sel.querySelector("select").options).find(o => o.value && o.value !== "—").value; sel.querySelector("input").value = "3";
  d.querySelector('[data-eqv="finalDelivery"]').value = "Brussels depot";
  d.getElementById("crmEqvSave").click(); await sleep(800);
  const saved = w.CRM_ENQUIRIES.find(x => x.id === "ENQ-TEST-1");
  check("saving keeps all container rows and the edited place", saved.containers.length === 3 && saved.finalDelivery === "Brussels depot" && saved.containerType === "20GP" && saved.containerQty === "2", JSON.stringify(saved.containers));
  open.click ? 0 : 0;
  d.querySelector('[data-crm-enq-open="ENQ-TEST-1"]').click(); await sleep(1200);
  check("reopening shows the three container rows", rows().length === 3, rows().join());
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
