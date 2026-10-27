// Air end to end: department follows Air Export / Air Import / Sea Import; carriers come from the masters; package dimensions -> chargeable weight -> job overview -> Air Waybill -> job cost line -> invoice / purchase bill lines.
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
  const org = await j("POST", "/api/organizations", { code: "AF" + tag.slice(-6).toUpperCase(), name: "Airflow " + tag, admin_email: `af${tag}@t.test`, admin_username: "af" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Ava" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "af" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = async (u, o) => { for (let i = 0; ; i++) { try { return await fetch(new URL(u, BASE).href, o); } catch (e) { if (i >= 2) throw e; await sleep(200); } } }; w.sessionStorage.setItem("esway_access_token", token); w.localStorage.setItem("esway_persistent_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(6500);
  // department follows mode and direction
  w.go("job-new"); await sleep(1500);
  const set = async (id, v) => { const e = d.getElementById(id); e.value = v; e.dispatchEvent(new w.Event("change", { bubbles: true })); await sleep(200); };
  const dept = () => d.getElementById("nj_dept").value || d.getElementById("nj_dept").selectedOptions[0].text;
  for (const [mode, dir, want] of [["Air", "Export", "AEX"], ["Air", "Import", "AIM"], ["Sea", "Import", "SIM"], ["Sea", "Export", "SEX"]]) { await set("nj_mode", mode); await set("nj_dir", dir); check(`${mode} ${dir} sets the department to ${want}`, dept().startsWith(want + " "), dept()); }
  // an Air job with dimensions
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA(CHENNAI)", pod: "DEFRA(FRANKFURT)", department: "AEX" }, token)).body.job_number;
  await j("POST", `/api/jobs/${job}/hbl`, { hbl_type: "HAWB" }, token);
  w.go("job-detail", { jobNo: job }); await sleep(3500);
  d.querySelector('[data-tab="jt2"]').click(); await sleep(500);
  // the user types the dimensions: 2 cartons 100 x 60 x 50 cm, 40 kg each (volumetric 100 kg, actual 80 kg)
  d.getElementById("addDimBtn").click(); await sleep(300);
  const row = d.querySelector("#dimTableBody tr:last-child"); const put = (c, v) => { const e = row.querySelector(c); e.value = v; e.dispatchEvent(new w.Event("input", { bubbles: true })); };
  put(".dim-pkg", "2"); put(".dim-l", "100"); put(".dim-w", "60"); put(".dim-h", "50"); put(".dim-awt", "40"); await sleep(300);
  check("the dimension table shows chargeable weight 100.00 kg (shipment total, rounded up to 0.5)", /100\.00/.test(d.getElementById("dimTotChg").textContent) && /greater of actual and volumetric/.test(d.getElementById("dimChgNote").textContent), d.getElementById("dimTotChg").textContent);
  check("the job overview shows the chargeable weight at once", /100\.0 KG/.test(d.getElementById("jd_chargeable").textContent) && d.getElementById("jd_chgRow").style.display !== "none", d.getElementById("jd_chargeable").textContent);
  const save = d.querySelector("#jt2 .jd-bar button, #jt2 [data-jd-save]"); if (save) { save.click(); await sleep(2000); }
  const saved = (await j("GET", `/api/jobs/${job}/details`, null, token)).body;
  check("the server keeps the same figures with the dimensions", saved.dims.totals && saved.dims.totals.chargeable_kg === 100 && saved.dims.totals.basis === "volumetric", JSON.stringify(saved.dims.totals));
  check("the Air Waybill chargeable weight field carries it", d.getElementById("jobAwb_chg").value === "100", d.getElementById("jobAwb_chg").value);
  // the Air Waybill print
  const rd = await w.BLDocKit.data(job);
  const awb = w.DocKit.awb(rd);
  check("the Air Waybill print shows the chargeable weight 100.0 and the dimensions", /100\.0/.test(awb) && /2 pcs/.test(awb), awb.length + " chars");
  // job cost: a Per Chargeable KG line
  const side = rate => ({ ccy: "INR", exch: "1", rate, tax_type: "Non-Taxable", tax_pct: "0", party: "", use_party: false });
  const put2 = await j("PUT", `/api/shipments/by-number/${job}/charges`, { lines: [{ charge_name: "Air Freight", qty: "1", uom: "Per Chargeable KG", sell: { ...side("350"), party: "Acme", use_party: true }, buy: { ...side("300"), party: "Skyline", use_party: true } }] }, token);
  check("a Per Chargeable KG charge takes quantity 100 from the dimensions (sell 35,000, buy 30,000)", put2.status === 200 && Number(put2.body.lines[0].qty) === 100 && put2.body.totals.ar === "35000.00" && put2.body.totals.ap === "30000.00", JSON.stringify(put2.body.totals));
  const inv = (await j("GET", `/api/documents/billing/job-lines?job_number=${job}&party=Acme&doc_type=invoice`, null, token)).body;
  const pur = (await j("GET", `/api/documents/billing/job-lines?job_number=${job}&party=Skyline&doc_type=purchase_invoice`, null, token)).body;
  check("the invoice and the purchase bill start from quantity 100 (chargeable weight)", Array.isArray(inv) && inv.length === 1 && Number(inv[0].qty) === 100 && Array.isArray(pur) && pur.length === 1 && Number(pur[0].qty) === 100, JSON.stringify([inv, pur]).slice(0, 200));
  // changed dimensions flow on
  await j("PUT", `/api/jobs/${job}/details/dims`, { data: { unit: "cm_kg", rows: [{ desc: "Cartons", pkg: "2", l: "100", w: "60", h: "50", awt: "60" }] } }, token);
  const after = (await j("GET", `/api/shipments/by-number/${job}/charges`, null, token)).body.lines[0];
  check("changing the dimensions later updates the job cost quantity (actual 120 kg now heavier)", Number(after.qty) === 120, after.qty);
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
