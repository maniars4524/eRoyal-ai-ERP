// Air EXPORT House Air Waybill: Master AWB from the Air Stock, the extra fields, rate lines, dimensions reflected, and the printed waybill; sea and air import jobs are untouched
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const BASE = "http://127.0.0.1:8010";
const env = Object.fromEntries(fs.readFileSync("../.env.qa", "utf8").split(/\r?\n/).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const j = async (method, path, body, token) => { const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined }); let b = null; try { b = await r.json(); } catch (e) {} return { status: r.status, body: b }; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (n, ok, x) => { console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + (x || ""))); if (!ok) failures++; };
(async () => {
  const dev = await j("POST", "/api/auth/login", { identifier: env.SUPER_ADMIN_USERNAME, password: env.SUPER_ADMIN_PASSWORD, portal: "developer" });
  const tag = Date.now().toString(36).toUpperCase().slice(-5);
  const org = await j("POST", "/api/organizations", { code: "AX" + tag, name: "Ax " + tag, admin_email: `ax${tag}@t.test`, admin_username: "ax" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "ax" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "TRIO SHIPPING SERVICES PVT LTD", address: "CHENNAI", iata_code: "14-3-5076" }, token);
  const code = "U" + tag.slice(0, 2);
  const al = await j("POST", "/api/network/airlines", { code, name: "Test Air " + tag, num_code: "603" }, token);
  await j("POST", "/api/network/stock-lines", { airline_id: al.body.id, bill_type: "MASTER", from_awb: "60134700", to_awb: "60134704" }, token);
  const mk = async (mode, direction) => (await j("POST", "/api/shipments", { mode, direction, container_mode: mode === "Sea" ? "FCL" : undefined, pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const job = await mk("Air", "Export"), jobImp = await mk("Air", "Import"), jobSea = await mk("Sea", "Export");
  const put = async (jn, sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(jn)}/details/${sec}`, { data }, token)).status;
  await put(job, "parties", { shipper: "Natural Remedies Pvt Ltd\n2 C, 202, Damodar Park,\nMumbai", consignee: "AGRO TRADE SRL S.P.A\nGenova" });
  await put(job, "cargo", { pkgs: "1", gwt: "678", cargoDesc: "TEST\nINV NO:INV0081 / 30/09/2025" });
  await put(job, "dims", { version: 2, rows: [{ l: "21", w: "42", h: "56", pkg: "1", awt: "678" }] });

  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1400 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const vis = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && e.offsetParent !== null; }, sel);
  const setv = (sel, v) => page.evaluate((sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, sel, v);
  const openBl = async jn => { await page.evaluate(n => window.go("job-detail", { jobNo: n }), jn); await sleep(2500); await page.evaluate(() => document.querySelector('.tbb[data-tab="jt3"]').click()); await sleep(1800); };

  // 1. other jobs are unchanged
  await openBl(jobImp);
  check("air IMPORT: no Air Stock picker, no extra export block", !(await vis("#jobAwbExp")) && !(await vis("#jobAwbMawbPickWrap")) && /manual entry/i.test(await text("#jobAwbMasterTitle")));
  await openBl(jobSea);
  check("sea export: the Air Waybill block is not shown at all", !(await vis("#jobAwbBlock")) && !(await vis("#jobAwbExp")));

  // 2. air export
  await openBl(job);
  check("air EXPORT: Master AWB title says Air Stock and the extra block shows", /Air Stock/i.test(await text("#jobAwbMasterTitle")) && await vis("#jobAwbExp"));
  await setv("#jobAwb_airline", "Test Air " + tag); await sleep(1800);
  const opts = await page.evaluate(() => [...document.querySelectorAll("#jobAwb_mawbPick option")].map(o => o.value).filter(Boolean));
  check("the picker offers the free numbers of the airline's Air Stock", opts.join() === ["603-60134700", "603-60134701", "603-60134702", "603-60134703", "603-60134704"].join(), opts.join());
  check("the stock summary says how many are free", /5 free of 5/.test(await text("#jobAwb_stockInfo")), await text("#jobAwb_stockInfo"));
  await page.select("#jobAwb_mawbPick", "603-60134702"); await sleep(400);
  check("choosing a number fills the Master AWB No (and it cannot be typed freehand)", await page.evaluate(() => { const i = document.querySelector("#jobAwb_mawb"); return i.value === "603-60134702" && i.readOnly; }));
  check("the dimensions of the Cargo tab are reflected on the BL", /21/.test(await text("#jobAwbDims")) && /42/.test(await text("#jobAwbDims")) && /56/.test(await text("#jobAwbDims")) && /Val/i.test(await text("#jobAwbDims")), await text("#jobAwbDims"));
  const row0 = await page.evaluate(() => [...document.querySelectorAll('#jobAwbLinesBody tr:first-child input, #jobAwbLinesBody tr:first-child select')].map(i => i.value).join("|"));
  check("the first rate line is filled from the cargo (pieces 1, gross 678)", /^1\|678\b/.test(row0), row0);
  await setv('[data-awbl="0"][data-f="chwt"]', "678"); await setv('[data-awbl="0"][data-f="rate"]', "10"); await setv('[data-awbl="0"][data-f="rc"]', "Q");
  check("the line total is calculated (678 x 10 = 6780)", await page.evaluate(() => document.querySelector('[data-awbl-total="0"]').value === "6780"));
  await page.evaluate(() => document.querySelector("#jobAwbAddLine").click()); await sleep(300);
  await setv('[data-awbl="1"][data-f="pcs"]', "2"); await setv('[data-awbl="1"][data-f="gwt"]', "10"); await setv('[data-awbl="1"][data-f="rc"]', "N"); await setv('[data-awbl="1"][data-f="itemNo"]', "0123"); await setv('[data-awbl="1"][data-f="chwt"]', "10"); await setv('[data-awbl="1"][data-f="rate"]', "5");
  for (const [id, v] of [["jobAwb_shipperLc", "LC-111"], ["jobAwb_consigneeLc", "LC-222"], ["jobAwb_to2", "BKK"], ["jobAwb_by2", "TG"], ["jobAwb_to3", "FRA"], ["jobAwb_by3", "LH"], ["jobAwb_flight2", "TG322"], ["jobAwb_flightDate2", "2024-10-31"], ["jobAwb_addNotif", "NOTIFY ON ARRIVAL"], ["jobAwb_shipperAcct", "SH-ACC-9"]]) await setv("#" + id, v);
  await setv("#jobAwb_wtval", "CC"); await setv("#jobAwb_otherFlag", "PP"); await setv("#jobAwb_chgsCode", "PC"); await setv("#jobAwb_flight", "UL603"); await setv("#jobAwb_flightDate", "2024-10-30"); await setv("#jobAwb_to", "SIN");
  await page.evaluate(() => document.querySelector('[data-jd-save="jt3"]').click()); await sleep(2500);
  const saved = (await j("GET", `/api/jobs/${encodeURIComponent(job)}/details`, null, token)).body.bl;
  check("saved: the Master AWB, the extra fields and both rate lines", saved.mblNo === "603-60134702" && saved.awb.shipperLc === "LC-111" && saved.awb.to2 === "BKK" && saved.awb.flight2 === "TG322" && saved.awb.wtval === "CC" && saved.awb.chgsCode === "PC" && saved.awb.lines.length === 2 && saved.awb.lines[0].total === "6780" && saved.awb.lines[1].total === "50" && saved.awb.rateClass === "Q", JSON.stringify(saved).slice(0, 400));
  const job2 = await mk("Air", "Export");
  const st = (await j("GET", `/api/jobs/${encodeURIComponent(job2)}/awb-stock?airline=${code}`, null, token)).body;
  check("the number taken is no longer offered to another job", !st.available.includes("603-60134702") && st.available.length === 4, st.available.join());
  check("and the same job still sees its own number", (await j("GET", `/api/jobs/${encodeURIComponent(job)}/awb-stock`, null, token)).body.current === "603-60134702");

  // 3. the printed waybill carries everything
  await page.evaluate(jn => { window.__lastJobNo = jn; window.DocKit.view("awb", { job: jn }); }, job); await sleep(3500);
  for (const fk of ["one", "two", "three"]) {
    await page.waitForSelector("#dkFmt", { timeout: 15000 }); await page.select("#dkFmt", fk); await sleep(3000); const t = await text("#docViewZone"), up = t.toUpperCase();
    const need = ["603-60134702", "LC-111", "LC-222", "SH-ACC-9", "BKK", "TG", "FRA", "LH", "UL603", "TG322", "NOTIFY ON ARRIVAL", "6,780.00", "50.00", "6,830.00", "21X42X56"];
    const master = fk === "one" ? "603 60134702" : fk === "two" ? "603-6013 4702" : ""; const miss = need.map(w => w === "603-60134702" ? master : w).filter(w => w && !up.includes(w.toUpperCase()));
    check("printed " + fk + ": master no, LC, account, 3 routing legs, 2 flights, rate lines, notification, dimensions", miss.length === 0, "MISSING: " + miss.join(", ") + " ;; " + t.replace(/\s+/g, " ").slice(0, 900));
  }
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
