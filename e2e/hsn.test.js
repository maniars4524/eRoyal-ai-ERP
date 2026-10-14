// Headless UI check #13: SAC / HSN Master loaded from the section-wise SAC list, live KPIs, linked charges, filters, detail round-trip.
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
  const org = await j("POST", "/api/organizations", { code: "HS" + tag.slice(-6).toUpperCase(), name: "Hsn " + tag, admin_email: `hs${tag}@t.test`, admin_username: "hs" + tag, admin_password: "Ui-Test-Passw0rd-1", admin_first_name: "H" }, dev.body.access_token);
  const token = (await j("POST", "/api/auth/login", { identifier: "hs" + tag, password: "Ui-Test-Passw0rd-1", portal: "erp", license_number: org.body.organization.license_number })).body.access_token;
  const dom = await JSDOM.fromURL(BASE + "/erp", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE).href, o); w.sessionStorage.setItem("esway_access_token", token); w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.scrollTo = () => {}; w.console.error = () => {}; } });
  const w = dom.window, d = w.document;
  await sleep(2500);
  const q = s => d.querySelector(s);
  const text = s => (q(s) || { textContent: "" }).textContent.replace(/\s+/g, " ");
  const setf = (el, v, ev) => { el.value = v; el.dispatchEvent(new w.Event(ev || "input", { bubbles: true })); };
  const rows = () => d.querySelectorAll("#s-hsn-master tbody tr[data-mk-row], #s-hsn-master tbody tr:not(.mk-empty)").length;
  const kpis = () => Array.from(d.querySelectorAll("#s-hsn-master .kpi")).map(k => k.textContent.replace(/\s+/g, " ").trim());

  w.go("hsn-master"); await sleep(1500);
  check("empty master says so honestly (no fake '142 preloaded' text)", /No codes yet/.test(text("#hsnFooter")) && !/142|preloaded/.test(text("#s-hsn-master")), text("#hsnFooter"));
  q("#hsnLoadSacBtn").click(); await sleep(4500);
  check("Load SAC Master adds the 571 section-wise codes", /Showing 571 of 571 codes/.test(text("#hsnFooter")), text("#hsnFooter"));
  check("footer reports the codes whose GST rate is still blank", /rate not set on \d+/.test(text("#hsnFooter")));
  check("list shows Section / Heading / Group for each code", /Sec 6 · 9965 · 99652/.test(text("#s-hsn-master")) && /Section \/ Heading/.test(text("#s-hsn-master thead")));
  const secOpts = Array.from(d.querySelectorAll("#hsnSectionFilter option")).map(o => o.value).filter(Boolean);
  check("section filter lists sections 5 to 9", secOpts.join() === "5,6,7,8,9", secOpts.join());
  setf(q("#hsnSectionFilter"), "6", "change"); await sleep(600);
  check("section 6 filter shows only that section", /Showing 80 of 571/.test(text("#hsnFooter")), text("#hsnFooter"));
  setf(q("#hsnGstFilter"), "18", "change"); await sleep(400);
  check("GST % filter works on top of the section filter", /Showing \d+ of 571/.test(text("#hsnFooter")) && !/Showing 79 of/.test(text("#hsnFooter")), text("#hsnFooter"));
  setf(q("#hsnGstFilter"), "", "change"); setf(q("#hsnSectionFilter"), "", "change"); await sleep(300);
  const search = q('#s-hsn-master input[placeholder*="earch"]'); setf(search, "996521"); await sleep(500);
  check("search finds a code", /996521/.test(text("#s-hsn-master tbody")) && /Showing 1 of 571/.test(text("#hsnFooter")), text("#hsnFooter"));
  setf(search, ""); await sleep(300);

  // linked charges
  await j("POST", "/api/masters/charge", { code: "OFR", name: "Ocean Freight", data: { hsn_code: "996521", gst_percent: "5" } }, token);
  await j("POST", "/api/masters/charge", { code: "ODD", name: "Odd Charge", data: { hsn_code: "123456", gst_percent: "18" } }, token);
  w.go("hsn-master"); await sleep(1800);
  check("KPIs are live: codes used by charges and charges without a valid code", /Total Codes.*571/.test(kpis()[0]) && /1/.test(kpis()[1]) && /1.*Charges without a valid HSN\/SAC/.test(kpis()[3]), kpis().join(" | "));
  setf(search, "996521"); await sleep(500);
  check("linked charge count is shown against the code", /Ocean Freight|>1</.test(q("#s-hsn-master tbody").innerHTML) && /<span class="b b-gr"[^>]*>1<\/span>/.test(q("#s-hsn-master tbody").innerHTML));
  setf(search, "");

  // detail round trip
  const rec = (await j("GET", "/api/masters/hsn?q=996521", null, token)).body[0];
  q(`#s-hsn-master [data-mk-edit="${rec.id}"]`).click(); await sleep(2200);
  check("detail screen shows this code (not a hard-coded 996511) with its section data", /996521/.test(text("#s-hsn-detail .pt")) && /Coastal and transoceanic/.test(text("#s-hsn-detail .ps")), text("#s-hsn-detail .pt") + " | " + text("#s-hsn-detail .ps"));
  check("linked charges card lists the charge", /Linked Charges \(1\)/.test(text("#s-hsn-detail")) && /Ocean Freight/.test(text("#s-hsn-detail")));
  const secInput = Array.from(d.querySelectorAll("#s-hsn-detail .fg")).find(fg => /^Section$/.test(fg.querySelector("label").textContent.trim())).querySelector("input");
  check("section, heading and group fields are filled", secInput.value === "6", secInput.value);
  const gst = Array.from(d.querySelectorAll("#s-hsn-detail .fg")).find(fg => /GST Rate/.test(fg.querySelector("label").textContent)).querySelector("select");
  setf(gst, Array.from(gst.options).find(o => /^5/.test(o.value)).value, "change");
  const cg = Array.from(d.querySelectorAll("#s-hsn-detail .fg")).find(fg => /^CGST/.test(fg.querySelector("label").textContent.trim())).querySelector("input");
  check("CGST / SGST follow the GST rate", cg.value === "2.5", cg.value);
  d.querySelector("#s-hsn-detail .pa .btn-suc").click(); await sleep(1800);
  const after = (await j("GET", `/api/masters/hsn/${rec.id}`, null, token)).body;
  check("saving keeps the section, heading and official description", after.data.section === "6" && after.data.heading === "9965" && after.data.group === "99652" && after.data.full_description && /5/.test(after.data.gst_rate), JSON.stringify(after.data));
  const again = (await j("POST", "/api/masters/hsn/sac-master/import", null, token)).body;
  check("re-loading the SAC master never duplicates or overwrites edits", again.created === 0 && again.skipped === 571);

  console.log(failures ? "\n" + failures + " FAILED" : "\nALL UI CHECKS PASSED");
  w.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error("ERROR", e); process.exit(2); });
