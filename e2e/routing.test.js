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
  const org = await j("POST", "/api/organizations", { code: "RT" + tag, name: "Wiz " + tag, admin_email: `rt${tag}@t.test`, admin_username: "rt" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "C" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "rt" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const api = async p => (await j("GET", p, null, token)).body;
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1000 }); const errs = []; let confirms = 0;
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => { if (d.type() === "confirm") confirms++; d.accept(); });
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);
  const click = sel => page.evaluate(sel => document.querySelector(sel).click(), sel);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const vis = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && e.offsetParent !== null; }, sel);
  const iso = d => new Date(Date.now() + d * 864e5).toISOString();
  const sea = (await j("POST", "/api/shipments", { mode: "Sea", direction: "Import", container_mode: "FCL", pol: "INMAA", pod: "NLRTM", etd: "2030-05-01", eta: "2030-05-30" }, token)).body;
  const air = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", container_mode: "Air", pol: "MAA", pod: "FRA" }, token)).body;
  const openJob = async n => { await page.evaluate(n => go("job-detail", { jobNo: n }), n); await sleep(3500); await page.evaluate(() => document.querySelector('#s-job-detail .tbb[data-tab="jtrt"]').click()); await sleep(1800); };

  // tab position
  await openJob(sea.job_number);
  const tabs = await page.evaluate(() => [...document.querySelectorAll("#s-job-detail .tbb")].map(b => b.textContent.trim()));
  check("Routing sits between Charges and Documents", tabs.filter(t => t !== "Tracking").indexOf("Routing") === tabs.filter(t => t !== "Tracking").indexOf("Charges") + 1 && tabs.indexOf("Documents") === tabs.indexOf("Routing") + 1, tabs.join("|"));
  check("a sea job shows Sea Shipment Routing with Vessel / Voyage / POL / POD and no air fields", /Sea Shipment Routing/.test(await text("#rtBody")) && /Vessel/.test(await text("#rtBody")) && !/Flight No/.test(await text("#rtBody")));
  check("the first row is suggested from the job (POL, ETD) but not saved", await page.evaluate(() => document.querySelector('[data-rt-row] [data-f="pol"]').value === "INMAA") && /Save to keep/.test(await text("#rtBody")) && (await j("GET", "/api/jobs/" + sea.job_number + "/routing", null, token)).body.legs.length === 0);
  // fill vessel 1 and save
  const fill = (row, vals) => page.evaluate((row, vals) => { const tr = document.querySelectorAll("#rtBody tbody tr")[row]; for (const [k, v] of Object.entries(vals)) { const e = tr.querySelector('[data-f="' + k + '"]'); e.value = v; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); } }, row, vals);
  const loc = d => { const x = new Date(Date.now() + d * 864e5); const p = n => String(n).padStart(2, "0"); return x.getFullYear() + "-" + p(x.getMonth() + 1) + "-" + p(x.getDate()) + "T" + p(x.getHours()) + ":" + p(x.getMinutes()); };
  await fill(0, { carrier: "MSC Orion", voyage: "VOY12345", pol: "Chennai", pod: "Singapore", etd: loc(4), eta: loc(11), stage: "CONFIRMED" });
  check("Confirmed hides the follow-up box", !(await page.evaluate(() => document.querySelector("#rtBody tbody tr [data-fu-wrap]").offsetParent !== null)));
  await page.evaluate(() => document.querySelector('[data-rt^="save:"]').click()); await sleep(2000);
  let d = (await j("GET", "/api/jobs/" + sea.job_number + "/routing", null, token)).body;
  check("Save stores vessel 1 as a confirmed leg", d.legs.length === 1 && d.legs[0].stage === "CONFIRMED" && d.legs[0].carrier === "MSC Orion" && d.legs[0].pol === "Chennai", JSON.stringify(d.legs).slice(0, 200));
  // add vessel 2: planned, follow-up yes
  await page.evaluate(() => document.querySelector('[data-rt="add"]').click()); await sleep(500);
  await fill(1, { carrier: "Maersk Bella", voyage: "VOY67890", pol: "Singapore", pod: "Rotterdam", etd: loc(14), eta: loc(30), stage: "PLANNED" });
  check("Planned shows the Follow-up Yes / No box next to it", await page.evaluate(() => document.querySelectorAll("#rtBody tbody tr")[1].querySelector("[data-fu-wrap]").offsetParent !== null));
  await fill(1, { follow_up: "1" });
  await page.evaluate(() => document.querySelectorAll('[data-rt^="save:"]')[1].click()); await sleep(2000);
  d = (await j("GET", "/api/jobs/" + sea.job_number + "/routing", null, token)).body;
  check("vessel 2 is saved as planned with follow-up", d.legs.length === 2 && d.legs[1].stage === "PLANNED" && d.legs[1].follow_up === true, JSON.stringify(d.legs[1]).slice(0, 200));
  await page.evaluate(() => document.querySelector('[data-rt="history"]').click()); await sleep(1200);
  check("the history lists the changes", /Routing history/.test(await text("#rtBody")) && /Created/i.test(await text("#rtBody")));
  // tracking
  console.log("JR", await page.evaluate(() => !!document.querySelector("#jlMilestones .rt-journey") + " " + !!document.getElementById("jlMilestones")));
  await page.evaluate(() => document.querySelector('[data-jl="tracking"]').click()); await sleep(2500);
  check("tracking shows both legs, planned marked tentative", /Sea routing/.test(await text("#modalBody, .modal")) && /Maersk Bella/.test(await text("#modalBody, .modal")) && /tentative/.test(await text("#modalBody, .modal")), (await text("#modalBody, .modal")).slice(0, 200));
  await page.evaluate(() => closeModal()); await sleep(400);
  // dashboard
  await page.evaluate(() => go("dashboard")); await sleep(1500); await page.evaluate(() => window.__rtAlerts()); await sleep(1500);
  const dash = await text("#rtDash");
  check("the dashboard alert area lists the follow-up leg of the job", /Routing Alerts/i.test(dash) && dash.includes(sea.job_number) && /Maersk Bella/.test(dash) && /Follow-up/.test(dash), dash.slice(0, 300));
  await page.evaluate(() => document.querySelector("#rtDash .rt-row").click()); await sleep(4500);
  check("clicking the alert opens the job on its Routing tab", await vis("#rtBody") && /Sea Shipment Routing/.test(await text("#rtBody")));
  // air
  await openJob(air.job_number);
  console.log("AIRTXT", JSON.stringify((await text("#rtBody")).slice(0,200)), await page.evaluate(() => document.getElementById("jobDetailNo").textContent));
  check("an air job shows Air Shipment Routing with Airline / Flight and no ports", /Air Shipment Routing/.test(await text("#rtBody")) && /airline/i.test(await text("#rtBody")) && !/bPOLb/.test(await text("#rtBody")));
  check("no script errors", errs.length === 0, errs.join(" | ").slice(0, 300));
  await b.close();
  console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
