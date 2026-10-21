// Headless UI check: House Air Waybill of an air job (number, entry, saved, produced from Out Docs).
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
  const org = await j("POST", "/api/organizations", { code: "AWB" + tag.slice(-4).toUpperCase(), name: "Awb " + tag, admin_email: `aw${tag}@t.test`, admin_username: "aw" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "A" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "aw" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "MAA(CHENNAI INTERNATIONAL AIRPORT)", pod: "FRA(FRANKFURT)" }, token)).body;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; w.print = () => {}; w.open = () => null; } });
  const w = dom.window, d = w.document; const q = s => d.querySelector(s);
  await sleep(3000); w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt3"]').click(); await sleep(500);
  check("an air job's BL / AWB tab shows the House Air Waybill block and hides the sea BL fields", q("#jobAwbBlock").style.display !== "none" && q("#jobBlHouseWrap").style.display === "none" && q("#jobBlHbltypeWrap").style.display === "none");
  check("the next HAWB number is previewed", /HAWB001MAAFRA/.test(q("#jobAwb_hawbNo").placeholder), q("#jobAwb_hawbNo").placeholder);
  q("#jobAwb_genBtn").click(); await sleep(1800);
  const no = q("#jobAwb_hawbNo").value;
  check("Generate HAWB issues company code + HAWB + running no + POL + POD", /^AWB.*HAWB001MAAFRA$/.test(no), no);
  const set = (id, v) => { q(id).value = v; q(id).dispatchEvent(new w.Event("input", { bubbles: true })); };
  set("#jobAwb_mawb", "020-12345678"); set("#jobAwb_airline", "Lufthansa"); set("#jobAwb_flight", "LH 757"); set("#jobAwb_rate", "185.5"); set("#jobAwb_to", "FRA"); set("#jobAwb_handling", "Keep dry");
  q('#jt3 [data-jd-save]').click(); await sleep(1800);
  const saved = (await j("GET", `/api/jobs/${job.job_number}/details`, null, token)).body;
  check("the AWB entry is saved with the House AWB number kept", saved.bl.mblNo === "020-12345678" && saved.bl.hblNo === no && saved.bl.awb.rate === "185.5" && saved.bl.awb.airline === "Lufthansa", JSON.stringify(saved.bl).slice(0, 200));
  await j("PUT", `/api/jobs/${job.job_number}/details/dims`, { data: { unit: "cm_kg", rows: [{ desc: "Carton", pkg: "10", l: "60", w: "40", h: "40", awt: "21" }] } }, token);
  await j("PUT", `/api/jobs/${job.job_number}/details/cargo`, { data: { pkgs: "10", gwt: "210", commodity: "Garments" } }, token);
  w.go("job-detail", { jobNo: job.job_number }); await sleep(3500);
  q('.tbb[data-tab="jt7b"]').click(); await sleep(2000);
  const row = Array.from(d.querySelectorAll("#odAirExportDocs [data-od=awb]"));
  check("Out Docs has the HAWB row with View / Print / PDF / Send", row.length === 4, row.length);
  row.find(b => b.dataset.act === "view").click(); await sleep(3500);
  const html = d.getElementById("docViewZone").innerHTML;
  check("the House Air Waybill opens as an A4 form with its number, master AWB, rate, dimensions and weights", /HOUSE AIR WAYBILL/.test(html) && html.includes(no) && /020-12345678/.test(html) && /60×40×40/.test(html) && /185\.50/.test(html) && /class="pg"/.test(html), html.slice(0, 200));
  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
