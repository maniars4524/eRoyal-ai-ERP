// Headless UI check #16: Port Master with the worldwide sea ports and airports (search, filters, paging) and port type-ahead in quotations.
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
  await j("POST", "/api/ports/import-global", null, dev.body.access_token);
  const tag = Date.now().toString(36);
  const org = await j("POST", "/api/organizations", { code: "PT" + tag.slice(-6).toUpperCase(), name: "Ports " + tag, admin_email: `pt${tag}@t.test`, admin_username: "pt" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "P" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "pt" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const pick = async (gridSel, value) => { const card = value ? q(`${gridSel} .wiz-card[data-pick="${value}"]`) : q(`${gridSel} .wiz-card`); card.click(); await sleep(250); };

  w.go("port-master"); await sleep(2500);
  check("the master shows the worldwide totals (sea ports, airports)", /21,528/.test(text("#portKpiTotal")) && Number(text("#portKpiSea").replace(/,/g, "")) > 15000 && Number(text("#portKpiAir").replace(/,/g, "")) > 3500, [text("#portKpiTotal"), text("#portKpiSea"), text("#portKpiAir")].join(" "));
  check("only one page of 50 rows is drawn, with the total", d.querySelectorAll("#portTableBody tr[data-port-row]").length === 50 && /Showing 1–50 of 21,528 ports/.test(text("#portCountLabel")), text("#portCountLabel"));
  check("type and country filters are filled from the data", d.querySelectorAll("#portTypeFilter option").length >= 3 && d.querySelectorAll("#portCountryFilter option").length > 200);
  setf(q("#portTypeFilter"), "Airport", "change"); await sleep(1200);
  check("the Airport filter shows scheduled-service airports only", /of 4,008 ports/.test(text("#portCountLabel")) && !/Sea/.test(text("#portTableBody").replace(/Seattle|Seoul|Sea[a-z]/g, "")), text("#portCountLabel"));
  setf(q("#portCountryFilter"), "India", "change"); await sleep(1200);
  check("filters combine (airports in India)", /Chennai International Airport/.test(text("#portTableBody")) || /of \d+ ports/.test(text("#portCountLabel")), text("#portCountLabel"));
  setf(q("#portTypeFilter"), "", "change"); setf(q("#portCountryFilter"), "", "change"); setf(q("#portSearch"), "chennai"); await sleep(1500);
  check("search finds both the Chennai sea port and the airport", /INMAA/.test(text("#portTableBody")) && /AirMAA/.test(text("#portTableBody")), text("#portTableBody").slice(0, 200));
  setf(q("#portSearch"), ""); await sleep(1300);
  q("#portNext").click(); await sleep(1200);
  check("Next page moves on (51–100) and Previous comes back", /Showing 51–100 of 21,528/.test(text("#portCountLabel")) && q("#portPrev").disabled === false);
  q("#portPrev").click(); await sleep(1000);
  check("back on the first page", /Showing 1–50/.test(text("#portCountLabel")) && q("#portPrev").disabled === true);

  // ---------- type-ahead on the quotation form
  w.go("quotation-new"); await sleep(1500);
  await pick("#quotWizModeGrid", "Sea"); q('#quotWizDirGrid .wiz-dir-pill[data-pick="Export"]').click(); await sleep(250); await pick("#quotWizTypeGrid", "FCL"); await pick("#quotWizQtypeGrid"); await pick("#quotWizCarrierGrid", "single");
  q("#quotWizNext").click(); await sleep(800);
  const fire = async (el, v, ev) => { el.dispatchEvent(new w.Event("focusin", { bubbles: true })); el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); await sleep(1300); };
  const optsOf = el => Array.from(d.querySelectorAll("#" + el.getAttribute("list") + " option")).map(o => o.value);
  const pol = q('[data-qh="pol"]');
  await fire(pol, "hamb");
  check("quotation From / To suggests ports as CODE(NAME)", optsOf(pol).some(v => v === "DEHAM(HAMBURG)"), optsOf(pol).slice(0, 5).join("|"));
  await fire(pol, "chennai");
  const seaOpts = optsOf(pol);
  check("a sea quotation offers sea ports only: typing chennai gives INMAA(CHENNAI) and no airport", seaOpts.includes("INMAA(CHENNAI)") && !seaOpts.some(v => /^MAA\(/.test(v)), seaOpts.join("|"));
  await fire(pol, "INMAA");
  check("typing the code finds the same port", optsOf(pol)[0] === "INMAA(CHENNAI)", optsOf(pol).join("|"));
  await fire(pol, "los angeles");
  check("worldwide ports come as CODE(NAME), e.g. USLAX(LOS ANGELES)", optsOf(pol).includes("USLAX(LOS ANGELES)"), optsOf(pol).join("|"));
  await fire(pol, "new york");
  check("USNYC(NEW YORK) is offered", optsOf(pol).includes("USNYC(NEW YORK)"), optsOf(pol).join("|"));
  await fire(pol, "chennai", "change"); 
  check("leaving the field with a name turns it into the canonical CODE(NAME)", pol.value === "INMAA(CHENNAI)", pol.value);
  w.quotWiz.mode = "Air"; w.QK.onWizardDone(); await sleep(400);
  const polAir = q('[data-qh="pol"]');
  await fire(polAir, "chennai");
  const airOpts = optsOf(polAir);
  check("an air quotation offers airports only: MAA(CHENNAI INTERNATIONAL AIRPORT), no sea port", airOpts.includes("MAA(CHENNAI INTERNATIONAL AIRPORT)") && !airOpts.some(v => /^INMAA\(/.test(v)), airOpts.join("|"));
  await fire(polAir, "maa", "change");
  check("typing MAA becomes MAA(CHENNAI INTERNATIONAL AIRPORT)", polAir.value === "MAA(CHENNAI INTERNATIONAL AIRPORT)", polAir.value);

  // ---------- the same picker on the CRM enquiry, job and booking forms
  w.go("crm-enquiry"); await sleep(1500); w.crmRenderEnquiryForm({}); await sleep(600);
  setf(q("#crmEq_mode"), "Air", "change");
  await fire(q("#crmEq_pol"), "singapore");
  check("CRM enquiry: air mode gives SIN(SINGAPORE CHANGI AIRPORT), not the sea port", optsOf(q("#crmEq_pol")).includes("SIN(SINGAPORE CHANGI AIRPORT)") && !optsOf(q("#crmEq_pol")).includes("SGSIN(SINGAPORE)"), optsOf(q("#crmEq_pol")).join("|"));
  setf(q("#crmEq_mode"), "Sea", "change");
  await fire(q("#crmEq_pod"), "singapore");
  check("CRM enquiry: sea mode gives SGSIN(SINGAPORE)", optsOf(q("#crmEq_pod")).includes("SGSIN(SINGAPORE)") && !optsOf(q("#crmEq_pod")).some(v => /^SIN\(/.test(v)), optsOf(q("#crmEq_pod")).join("|"));
  w.jobWiz.mode = "Sea"; w.go("job-new"); await sleep(1000);
  await fire(q("#nj_pod"), "antwerp");
  check("New Job: Port of Discharge searches the Port Master", optsOf(q("#nj_pod")).includes("BEANR(ANTWERPEN)") || optsOf(q("#nj_pod")).some(v => /^BEANR\(/.test(v)), optsOf(q("#nj_pod")).join("|"));
  w.bookWiz.mode = "Air"; w.go("booking-new"); await sleep(1000);
  await fire(q("#bk_pol"), "dubai");
  check("New Booking: an air booking offers DXB(DUBAI INTERNATIONAL AIRPORT) only", optsOf(q("#bk_pol")).includes("DXB(DUBAI INTERNATIONAL AIRPORT)") && !optsOf(q("#bk_pol")).some(v => /^AEDXB|^AEJEA/.test(v)), optsOf(q("#bk_pol")).join("|"));

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
