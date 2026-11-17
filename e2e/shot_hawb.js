const puppeteer = require("puppeteer-core"); const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (m, p, b, t) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) }, body: b ? JSON.stringify(b) : undefined }); let x = null; try { x = await r.json(); } catch (e) {} return { status: r.status, body: x }; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "SH" + tag, name: "Sh " + tag, admin_email: `sh${tag}@t.test`, admin_username: "sh" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "M", admin_last_name: "R" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "sh" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI", iata_code: "14-3-5076" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "MVMLE" }, token)).body.job_number;
  const put = async (sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(job)}/details/${sec}`, { data }, token)).status;
  await put("parties", { shipper: "MANITOU SOUTH ASIA PVT LTD\n128, DEVELOPED PLOTS\nESTATE MAIN ROAD\nPERUNGUDI INDUSTRIAL ESTATE 600096\nCHENNAI INDIA", consignee: "WASTE MANAGEMENT CORPORATION LIMITED\n3RD FLOOR, MA.JAMBUGASDHOSHUGE, MAJEEDHEE\nMAGU, 20161,MALE, REPUBLIC OF MALDIVES\nREG: C-48/2009,PHONE: +960 7970898" });
  await put("bl", { mblNo: "603-60134734", hblNo: "RTSCHNAWBL0006", blStage: "Released", awb: { airline: "UL", flight: "UL", payment: "Prepaid", handling: "[1] ONE PACKAGE ONLY.\nMARKED AS ADDRESSED //ONE COVER ENCLOSED//", iata: "14-3-5076", routingTo: "CMB", currency: "INR", declCarriage: "NVD", declCustoms: "NCV", insurance: "XXX", rate: "200", otherCharges: "525", otherText: "CGC : 75/-  MCC : 150/-  SC : 300/-", chargeableWt: "88", execDate: "2023-07-13", place: "CHENNAI" } });
  await put("cargo", { pkgs: "1", gwt: "55.00", cargoDesc: "SAID TO CONTAIN\nSPARE PARTS OF MANITOU FORKLIFT\nINV NO:IB7232000201" });
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1500 });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await new Promise(r => setTimeout(r, 3000));
  await page.evaluate(jn => { window.__lastJobNo = jn; window.DocKit.view("awb", { job: jn }); }, job); await new Promise(r => setTimeout(r, 3500));
  for (const fk of ["one", "two", "three"]) {
    await page.select("#dkFmt", fk); await new Promise(r => setTimeout(r, 2800));
    const pgs = await page.$$("#docViewZone .pg");
    for (let i = 0; i < pgs.length; i++) await pgs[i].screenshot({ path: `../scratch/hawbpg_${fk}_${i + 1}.png` });
  }
  await b.close();
})();
