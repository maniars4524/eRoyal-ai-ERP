// House Air Waybill: the three formats after the owner's samples, the format bar (name, default) and the same data in each
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
  const org = await j("POST", "/api/organizations", { code: "HW" + tag, name: "Hw " + tag, admin_email: `hw${tag}@t.test`, admin_username: "hw" + tag.toLowerCase(), admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "Mani", admin_last_name: "Rs" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "hw" + tag.toLowerCase(), password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  await j("PUT", "/api/company/doc-profile", { legal_name: "ISPEED FREIGHT ( INDIA ) PVT LTD", address: "No.3/30, 4th floor, Medavakkam main road, Chennai-600091.", phone: "+91 44 4554 2779", iata_code: "14-3-5076", city: "CHENNAI", country: "INDIA" }, token);
  const job = (await j("POST", "/api/shipments", { mode: "Air", direction: "Export", pol: "INMAA", pod: "SGSIN" }, token)).body.job_number;
  const put = async (sec, data) => (await j("PUT", `/api/jobs/${encodeURIComponent(job)}/details/${sec}`, { data }, token)).status;
  const s1 = await put("parties", { shipper: "Inflow Technology Pvt Ltd\nC/O CWC Import Pvt Ltd, No. 185/1, Adam Nagar,\nNagalkeni, Chennai - 600044.", consignee: "Allied Telesis Asia Pacific Pte Ltd\n11 Tai Seng Link 4th floor FSC department\nSingapore 534182\nContact person: Kelvin/Boon hock/ Irene" });
  const s2 = await put("bl", { mblNo: "618-6316 1265", hblNo: "23240016", blStage: "Released", awb: { airline: "Singapore Airlines", flight: "SQ529", flightDate: "2023-06-28", payment: "Prepaid", totalMode: "agreed", handling: "PLEASE INFORM CONSIGNEE IMMEDIATELY ON ARRIVAL OF CARGO AT DESTINATION", iata: "14-3-5076", routingTo: "SIN", currency: "INR", declCarriage: "NVD", declCustoms: "NVC", insurance: "XXX", optional: "TERMS:DDP", accounting: "FREIGHT PREPAID", execDate: "2023-06-27", place: "Chennai[MAA]" } });
  const s3 = await put("cargo", { pkgs: "11", gwt: "62.00", cargoDesc: "NON-CARRIER ETHERNET SWITCH\nInvoice Number : ATI PL-23-1\nDATE 30-05-2023" });
  check("job data saved", s1 === 200 && s2 === 200 && s3 === 200, [s1, s2, s3].join());
  const b = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await b.newPage(); await page.setViewport({ width: 1500, height: 1200 }); const errs = [];
  page.on("pageerror", e => errs.push(String(e.stack || e.message).split(String.fromCharCode(10)).slice(0, 3).join(" <- ")));
  page.on("dialog", d => d.accept());
  await page.goto(BASE + "/erp/login", { waitUntil: "networkidle2" }); await page.evaluate(t => localStorage.setItem("esway_persistent_token", t), token); await page.reload({ waitUntil: "networkidle2" }); await sleep(3000);
  const text = sel => page.evaluate(sel => (document.querySelector(sel) || { innerText: "" }).innerText, sel);
  const view = async () => { await page.evaluate(jn => { window.__lastJobNo = jn; window.DocKit.view("awb", { job: jn }); }, job); await sleep(3500); };
  await view();
  const opts = await page.evaluate(() => [...document.querySelectorAll("#dkFmt option")].map(o => o.text).join("|"));
  check("the preview offers the existing layout and Format one, two and three", /Existing layout/.test(opts) && /Format one/.test(opts) && /Format two/.test(opts) && /Format three/.test(opts), opts);
  const pick = async v => { await page.select("#dkFmt", v); await sleep(3000); };
  const want0 = ["23240016", "Allied Telesis Asia Pacific", "Inflow Technology", "ISPEED FREIGHT", "SQ529", "SIN", "ETHERNET SWITCH", "AS AGREED"]; const wantFor = fk => fk === "three" ? want0 : want0.concat(["618-6316 1265"]);
  for (const [fk, label, extra] of [["one", "Format one", ["ORIGINAL 3 (FOR SHIPPER)", "MAWB NO:", "HAWB NO:"]], ["two", "Format two", ["MAWB NO.:", "HAWB No:", "Shipper Account Number"]], ["three", "Format three", ["ORIGINAL 3 ( FOR SHIPPER )", "HAWB NO:", "CONDITIONS OF CONTRACT"]]]) {
    await pick(fk); const t = await text("#docViewZone"); const up = t.toUpperCase(); const want = wantFor(fk);
    check(label + ": prints the job's data and its own wording", want.every(w => up.includes(w.toUpperCase())) && extra.every(w => t.includes(w) || up.includes(w.toUpperCase())), want.filter(w => !up.includes(w.toUpperCase())).concat(extra.filter(w => !t.includes(w) && !up.includes(w.toUpperCase()))).join(", "));
    await page.screenshot({ path: `../scratch/hawb_${fk}.png`, fullPage: false });
  }
  await pick("three"); const t3 = await text("#docViewZone");
  check("conditions page is exact: numbering, wording and closing sentence of the sample", ["NOTICE CONCERNING CARRIER'S LIMITATION OF LIABILITY", "CONDITIONS OF CONTRACT", "1.1.1", "2.2.2.5", "5.5.1", "6 6.1", "7 7.1", "10.1.3", "place of estination", "arrrival stated therein", "increase the limitation liability", "IF THIS AIR WAY BILL IS ISSUED BY A FORWARDER IN A CAPACITY AS CONTRACTING CARRIER", "GENERAL CONDITIONS AS APPROPRIATE."].every(w => t3.includes(w)), t3.slice(-600));
  check("the face wording follows each sample (Format two: AirWaybill / Registrations, Format one: property decribed, Format three: Dangerous Good Regulations)", await (async () => { await pick("two"); const a = await text("#docViewZone"); await pick("one"); const b2 = await text("#docViewZone"); return /AirWaybill/.test(a) && /Dangerous Goods Registrations/.test(a) && /property decribed/.test(b2) && /Dangerous Good Regulations/.test(t3); })());
  const fits = async () => page.evaluate(() => [...document.querySelectorAll("#docViewZone .pg")].map(p => p.scrollHeight <= p.clientHeight + 2).every(Boolean));
  for (const fk of ["one", "two", "three"]) { await pick(fk); check(fk + ": every page fits the A4 page (nothing cut off)", await fits()); }
  const pages = async () => page.evaluate(() => document.querySelectorAll("#docViewZone .pg").length);
  await pick("one"); const n1 = await pages(); await pick("two"); const n2 = await pages(); await pick("three"); const n3 = await pages();
  check("page counts: one = 1, two = 1, three = 2", n1 === 1 && n2 === 1 && n3 === 2, [n1, n2, n3].join());
  // name and save
  await page.evaluate(() => document.querySelector("#dkFmtName").click()); await sleep(700);
  await page.evaluate(() => { document.querySelector("#dkFn1").value = "Sri Lankan style"; document.querySelector("#dkFn2").value = "Typed with logo"; document.querySelector("#dkFn3").value = "System"; document.querySelector("#dkFnDef").value = "two"; document.querySelector("#dkFnSave").click(); }); await sleep(2800);
  check("the new names show in the list and the default is marked", await page.evaluate(() => { const o = [...document.querySelectorAll("#dkFmt option")].map(x => x.text).join("|"); return /Sri Lankan style/.test(o) && /Typed with logo \(default\)/.test(o) && /System/.test(o); }));
  const saved = (await j("GET", "/api/company/hawb-formats", null, token)).body;
  check("names and default are saved for the company", saved.default === "two" && saved.names.one === "Sri Lankan style", JSON.stringify(saved));
  await page.evaluate(jn => { window.__lastJobNo = jn; window.DocKit.view("awb", { job: jn }); }, job); await sleep(3500);
  check("a new preview opens in the saved default format", await page.evaluate(() => document.querySelector("#dkFmt").value === "two"));
  check("no script errors", errs.length === 0, errs.join(" | "));
  await b.close(); console.log(failures ? failures + " FAILED" : "ALL PASSED"); process.exit(failures ? 1 : 0);
})();
